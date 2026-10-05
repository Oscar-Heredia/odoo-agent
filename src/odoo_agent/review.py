"""Cross-check with the database's own AI: `ai.agent.get_direct_response` (Odoo AI app).

It is the only call that is not a pure read: it runs in a transaction that gets
committed, and the AI agent can use the tools of its topics. That is why an
agent is only queried if all its topics and tools are on the read-only
whitelist. If there is none, or the call fails, the text is returned for the
user to paste by hand into the AI chat (manual mode).
"""

import re
import time
from dataclasses import dataclass, field

from .odoo import OdooClient, OdooError

# Below the 120 s after which Claude Code moves an MCP call to the background (and
# the agent would carry on without waiting for the answer).
AI_TIMEOUT = 100.0
# Minimum time worth giving a fallback agent; with less, manual mode is better.
MIN_ATTEMPT = 20.0
# Standard topics of Ask AI (`ai.ai_agent_natural_language_search`) whose tools only
# read or open views. Checked on pruebas11-grupogr (19.0+e) on 2026-10-05
# (docs/spike.md).
READONLY_TOPICS = frozenset({
    "ai.ai_topic_natural_language_query",
    "ai.ai_topic_information_retrieval_query",
})
# Standard tool -> the only method it may call. Each tool's code is compared with
# that call: a tool edited in the database stops counting as read-only even if it
# keeps its xmlid. The `_ai_tool_*` methods live in enterprise/ai; reading their
# bodies is still pending (docs/spike.md).
READONLY_TOOLS = {
    "ai.ir_actions_server_adjust_search": "_ai_tool_adjust_search",
    "ai.ir_actions_server_compute_report_measures": "_ai_tool_compute_report_measures",
    "ai.ir_actions_server_get_fields": "_ai_tool_get_fields",
    "ai.ir_actions_server_get_menu_details": "_ai_tool_get_menu_details",
    "ai.ir_actions_server_open_menu_graph": "_ai_tool_open_menu_graph",
    "ai.ir_actions_server_open_menu_kanban": "_ai_tool_open_menu_kanban",
    "ai.ir_actions_server_open_menu_list": "_ai_tool_open_menu_list",
    "ai.ir_actions_server_open_menu_pivot": "_ai_tool_open_menu_pivot",
    "ai.ir_actions_server_read_group": "_ai_tool_read_group",
    "ai.ir_actions_server_search": "_ai_tool_search",
}
# If the profile does not set `ai_agent`, an agent that can read the database
# (read-only topics) is preferred, then this standard agent without topics.
DEFAULT_AGENT_XMLID = "ai.ai_default_agent"

REVIEW_FORMAT = """Review each step and answer in exactly this format:

### Step N: CORRECT | WITH CONCERNS | INCORRECT
Reason in one or two sentences. If you checked it in the database, say what you queried.

### Risks
### Better alternative (only if there is one)"""


@dataclass
class AgentCheck:
    id: int
    name: str
    xmlid: str | None
    topics: list[str]
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.problems


async def check_agents(client: OdooClient) -> list[AgentCheck]:
    """Checks the database's AI agents: which ones can be queried with no risk of writes."""
    agents = await client.read("ai.agent", "search_read", {"domain": [], "fields": ["name", "topic_ids"], "limit": 100})
    topic_ids = sorted({topic_id for agent in agents for topic_id in agent["topic_ids"]})
    topics = {}
    if topic_ids:
        rows = await client.read(
            "ai.topic", "search_read", {"domain": [["id", "in", topic_ids]], "fields": ["name", "tool_ids"], "limit": 500}
        )
        topics = {row["id"]: row for row in rows}
    tool_ids = sorted({tool_id for topic in topics.values() for tool_id in topic["tool_ids"]})
    xmlids = await _xmlids(client, {"ai.agent": [a["id"] for a in agents], "ai.topic": topic_ids, "ir.actions.server": tool_ids})
    code = {}
    if tool_ids:
        rows = await client.read(
            "ir.actions.server", "search_read", {"domain": [["id", "in", tool_ids]], "fields": ["code"], "limit": 500}
        )
        code = {row["id"]: row["code"] or "" for row in rows}

    checks = []
    for agent in agents:
        check = AgentCheck(
            id=agent["id"],
            name=agent["name"],
            xmlid=xmlids.get(("ai.agent", agent["id"])),
            topics=[topics[t]["name"] for t in agent["topic_ids"] if t in topics],
        )
        for topic_id in agent["topic_ids"]:
            topic = topics.get(topic_id)
            topic_xmlid = xmlids.get(("ai.topic", topic_id))
            name = topic["name"] if topic else f"id {topic_id}"
            if topic_xmlid not in READONLY_TOPICS:
                check.problems.append(f"topic «{name}» is not verified as read-only")
                continue
            for tool_id in topic["tool_ids"]:
                method = READONLY_TOOLS.get(xmlids.get(("ir.actions.server", tool_id)))
                if method is None:
                    check.problems.append(f"topic «{name}» has a tool (action {tool_id}) that is not a standard read-only one")
                elif not is_standard_tool_code(code.get(tool_id, ""), method):
                    check.problems.append(f"tool {tool_id} of topic «{name}» does not have the standard code (call {method})")
        checks.append(check)
    return checks


def is_standard_tool_code(code: str, method: str) -> bool:
    """Whether the code is only `ai['result'] = record.<method>(arguments)`, as in enterprise."""
    pattern = rf"ai\[['\"]result['\"]\]\s*=\s*record\.{re.escape(method)}\(\s*[\w\s,]*\)"
    return re.fullmatch(pattern, code.strip()) is not None


async def ask(
    client: OdooClient,
    facts: dict,
    problem: str,
    steps: list[str],
    evidence: str,
    models: list[str],
    previous_review: str | None = None,
) -> str:
    """Asks the database's AI for its opinion; if that is not possible, returns the text for manual mode."""
    prompt = build_prompt(problem, steps, evidence, previous_review)
    context_message = build_context_message(facts, client.profile, models)
    if not facts.get("ai_installed", True):
        return "NO REVIEW POSSIBLE: this database does not have the AI app. Deliver the proposal marked as NOT REVIEWED."
    try:
        agents, reason = await pick_agents(client)
    except OdooError as e:
        agents, reason = [], f"the AI agents could not be checked ({e})"
    if not agents:
        return manual(reason, context_message, prompt)
    failures = []
    # AI_TIMEOUT is the budget for the whole review, not for each agent.
    deadline = time.monotonic() + AI_TIMEOUT
    for agent in agents:
        remaining = deadline - time.monotonic()
        if remaining < MIN_ATTEMPT:
            failures.append(f"«{agent.name}»: no time left to try it")
            break
        try:
            replies = await client.direct_response(agent.id, prompt, context_message, remaining)
        except OdooError as e:
            # Ask AI calls Gemini with a 30 s timeout inside Odoo; if no answer comes,
            # the next agent is tried before falling back to manual mode.
            failures.append(f"«{agent.name}»: {e}")
            continue
        answer = "\n\n".join(r for r in replies if isinstance(r, str)) if isinstance(replies, list) else str(replies)
        reads = "can read the database" if agent.topics else "no data access: answers from what it knows about Odoo"
        header = f"AUTOMATIC REVIEW by AI agent «{agent.name}» (id {agent.id}) of this database; {reads}."
        if failures:
            header += f"\nFailed first: {'; '.join(failures)}."
        return f"{header}\n\n{answer}"
    return manual(f"the query failed with every usable agent ({'; '.join(failures)})", context_message, prompt)


async def pick_agents(client: OdooClient) -> tuple[list[AgentCheck], str]:
    """Agents that can be queried, in order of preference; or none, with the reason."""
    checks = await check_agents(client)
    wanted = client.profile.ai_agent
    if wanted:
        match = next((c for c in checks if c.name == wanted), None)
        if match is None:
            return [], f"the AI agent «{wanted}» set in the profile does not exist"
        if not match.usable:
            return [], f"the AI agent «{wanted}» does not pass the check: {'; '.join(match.problems)}"
        return [match], ""
    usable = sorted((c for c in checks if c.usable), key=lambda c: (not c.topics, c.xmlid != DEFAULT_AGENT_XMLID, c.id))
    if not usable:
        details = "; ".join(f"«{c.name}»: {', '.join(c.problems)}" for c in checks) or "there are no AI agents"
        return [], f"no AI agent passes the read-only check ({details})"
    return usable, ""


def build_prompt(problem: str, steps: list[str], evidence: str, previous_review: str | None) -> str:
    lines = [
        "Technical review of a proposed change for this Odoo database.",
        "",
        "## Problem",
        problem.strip(),
        "",
        "## Evidence already checked in the database",
        evidence.strip(),
        "",
        "## Proposed steps",
        *(f"{number}. {step.strip()}" for number, step in enumerate(steps, 1)),
    ]
    if previous_review:
        lines += ["", "## Your previous review and what was changed", previous_review.strip()]
    lines += ["", REVIEW_FORMAT]
    return "\n".join(lines)


def build_context_message(facts: dict, profile, models: list[str]) -> str:
    return (
        "You act as an Odoo technical reviewer for a developer who will apply the changes themselves. "
        f"Database: Odoo {facts.get('version', '?')}, hosting {profile.hosting}, environment {profile.env}. "
        f"Models involved: {', '.join(models) or 'not given'}. "
        "If you have read tools, use them to check fields, records or configuration; do not modify anything. "
        "Be concrete and brief. If you cannot check something, say so instead of assuming it."
    )


def manual(reason: str, context_message: str, prompt: str) -> str:
    return (
        "MANUAL MODE: REVIEW PENDING.\n"
        f"Reason: {reason}.\n\n"
        "Ask the user to paste this text into this database's AI chat (the «Ask AI» button, or Discuss with an AI agent) "
        "and to paste the answer back to you. Until then, the proposal stays REVIEW PENDING.\n\n"
        "----- text to paste -----\n"
        f"{context_message}\n\n{prompt}\n"
        "----- end -----"
    )


async def _xmlids(client: OdooClient, ids_by_model: dict[str, list[int]]) -> dict[tuple[str, int], str]:
    xmlids = {}
    for model, ids in ids_by_model.items():
        if not ids:
            continue
        rows = await client.read(
            "ir.model.data",
            "search_read",
            {"domain": [["model", "=", model], ["res_id", "in", ids]], "fields": ["module", "name", "res_id"], "limit": 500},
        )
        for row in rows:
            xmlids[(model, row["res_id"])] = f"{row['module']}.{row['name']}"
    return xmlids
