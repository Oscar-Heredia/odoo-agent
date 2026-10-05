"""Cotejo con la IA de la propia base: `ai.agent.get_direct_response` (app IA de Odoo).

Es la única llamada que no es pura lectura: corre en una transacción que se
confirma, y el agente IA puede usar las herramientas de sus temas. Por eso solo
se consulta a un agente cuyos temas y herramientas estén todos en la lista
blanca de solo lectura. Si no hay ninguno, o la llamada falla, se devuelve el
texto para que el usuario lo pegue a mano en el chat de la IA (modo manual).
"""

from dataclasses import dataclass, field

from .odoo import OdooClient, OdooError

# Por debajo de los 120 s a partir de los cuales Claude Code pasa una llamada MCP
# a segundo plano (y el agente seguiría sin esperar la respuesta).
AI_TIMEOUT = 100.0
# Temas y herramientas de serie que solo leen, por xmlid. Salen del código de
# enterprise de cada versión (docs/spike.md). Vacías: solo se consulta a agentes
# sin temas.
READONLY_TOPICS: frozenset[str] = frozenset()
READONLY_TOOLS: frozenset[str] = frozenset()
# Si el perfil no fija `ai_agent`, se prefiere este agente de serie.
DEFAULT_AGENT_XMLID = "ai.ai_default_agent"

REVIEW_FORMAT = """Revisa cada paso y contesta exactamente con este formato:

### Paso N: CORRECTO | CON REPAROS | INCORRECTO
Motivo en una o dos frases. Si lo comprobaste en la base, di qué consultaste.

### Riesgos
### Alternativa mejor (solo si la hay)"""


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
    """Revisa los agentes IA de la base: cuáles se pueden consultar sin riesgo de escritura."""
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
                check.problems.append(f"el tema «{name}» no está verificado como de solo lectura")
                continue
            for tool_id in topic["tool_ids"]:
                if xmlids.get(("ir.actions.server", tool_id)) not in READONLY_TOOLS:
                    check.problems.append(f"el tema «{name}» tiene una herramienta (acción {tool_id}) que no es de serie de solo lectura")
        checks.append(check)
    return checks


async def ask(
    client: OdooClient,
    facts: dict,
    problem: str,
    steps: list[str],
    evidence: str,
    models: list[str],
    previous_review: str | None = None,
) -> str:
    """Pide su opinión a la IA de la base; si no se puede, devuelve el texto para el modo manual."""
    prompt = build_prompt(problem, steps, evidence, previous_review)
    context_message = build_context_message(facts, client.profile, models)
    if not facts.get("ai_installed", True):
        return (
            "SIN COTEJO POSIBLE: esta base no tiene la app IA. Entrega la propuesta marcada como NO COTEJADA."
        )
    try:
        agent, reason = await pick_agent(client)
    except OdooError as e:
        agent, reason = None, f"no se pudieron revisar los agentes IA ({e})"
    if agent is None:
        return manual(reason, context_message, prompt)
    try:
        replies = await client.direct_response(agent.id, prompt, context_message, AI_TIMEOUT)
    except OdooError as e:
        return manual(f"la consulta al agente «{agent.name}» falló: {e}", context_message, prompt)
    answer = "\n\n".join(r for r in replies if isinstance(r, str)) if isinstance(replies, list) else str(replies)
    return f"COTEJO AUTOMÁTICO con el agente IA «{agent.name}» (id {agent.id}) de esta base.\n\n{answer}"


async def pick_agent(client: OdooClient) -> tuple[AgentCheck | None, str]:
    checks = await check_agents(client)
    wanted = client.profile.ai_agent
    if wanted:
        match = next((c for c in checks if c.name == wanted), None)
        if match is None:
            return None, f"no existe el agente IA «{wanted}» que indica el perfil"
        if not match.usable:
            return None, f"el agente IA «{wanted}» no pasa la revisión: {'; '.join(match.problems)}"
        return match, ""
    usable = sorted((c for c in checks if c.usable), key=lambda c: (c.xmlid != DEFAULT_AGENT_XMLID, c.id))
    if not usable:
        details = "; ".join(f"«{c.name}»: {', '.join(c.problems)}" for c in checks) or "no hay agentes IA"
        return None, f"ningún agente IA pasa la revisión de solo lectura ({details})"
    return usable[0], ""


def build_prompt(problem: str, steps: list[str], evidence: str, previous_review: str | None) -> str:
    lines = [
        "Revisión técnica de una propuesta de cambio para esta base de Odoo.",
        "",
        "## Problema",
        problem.strip(),
        "",
        "## Evidencia ya comprobada en la base",
        evidence.strip(),
        "",
        "## Pasos propuestos",
        *(f"{number}. {step.strip()}" for number, step in enumerate(steps, 1)),
    ]
    if previous_review:
        lines += ["", "## Tu revisión anterior y lo que se cambió", previous_review.strip()]
    lines += ["", REVIEW_FORMAT]
    return "\n".join(lines)


def build_context_message(facts: dict, profile, models: list[str]) -> str:
    return (
        "Actúas como revisor técnico de Odoo para un desarrollador que aplicará los cambios él mismo. "
        f"Base: Odoo {facts.get('version', '?')}, hosting {profile.hosting}, entorno {profile.env}. "
        f"Modelos implicados: {', '.join(models) or 'no indicados'}. "
        "Si tienes herramientas de lectura, úsalas para comprobar campos, registros o configuración; no modifiques nada. "
        "Sé concreto y breve. Si no puedes comprobar algo, dilo en vez de suponerlo."
    )


def manual(reason: str, context_message: str, prompt: str) -> str:
    return (
        "MODO MANUAL: PENDIENTE DE COTEJO.\n"
        f"Motivo: {reason}.\n\n"
        "Pide al usuario que pegue este texto en el chat de la IA de esta base (Conversaciones, con un agente IA) "
        "y que te pegue la respuesta. Hasta entonces, la propuesta queda PENDIENTE DE COTEJO.\n\n"
        "----- texto para pegar -----\n"
        f"{context_message}\n\n{prompt}\n"
        "----- fin -----"
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
