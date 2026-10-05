"""odoo-agent MCP server: reads an Odoo database and cross-checks with its AI.

A session works with a single database. Every read goes through the guard
(whitelist) and everything returned to the agent goes through guard.render.
"""

import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import guard, review
from .odoo import OdooClient, OdooError, Session, source_branch

SOURCES_ROOT = Path(os.environ.get("ODOO_SRC_ROOT") or Path.home() / "dev" / "odoo-src")
KEY_MODULES = (
    "web_enterprise",
    "ai",
    "ai_app",
    "web_studio",
    "base_import_module",
    "account_accountant",
    "sale_management",
    "purchase",
    "stock",
    "mrp",
    "website",
    "point_of_sale",
    "hr",
    "project",
    "crm",
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

# One line per HTTP request is just noise in the MCP log.
logging.getLogger("httpx2").setLevel(logging.WARNING)

mcp = MCPServer(
    "odoo",
    log_level="WARNING",
    instructions=(
        "Reads an Odoo 19+ database over JSON-2, always read-only, and cross-checks with that same database's AI. "
        "One session, one database: start by calling server_info with the profile."
    ),
)
session = Session()


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def server_info(profile: str | None = None) -> str:
    """Pins this session's database and summarizes it. Without a profile, lists the available profiles.

    Returns version, edition, hosting and environment, key and custom modules (neither from Odoo nor in
    its sources), companies, AI app agents (and whether they can be queried) and where the sources
    for that version are. Once pinned, the session cannot switch databases.
    """

    async def run():
        if profile is None and session.client is None and not session.preset and session.env_path is None:
            return {
                "profiles": [
                    {"name": p.name, "url": p.url, "hosting": p.hosting, "env": p.env}
                    for p in session.profiles().values()
                ],
                "next": "Call server_info with the profile of the database you will work with.",
            }
        client = session.bind(profile) if profile else session.current()
        return await _describe(client)

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def search_read(
    model: str,
    fields: list[str],
    domain: list[Any] | None = None,
    limit: int = guard.DEFAULT_LIMIT,
    offset: int = 0,
    order: str | None = None,
    count_only: bool = False,
    company_ids: list[int] | None = None,
) -> str:
    """Reads records of a model, or only counts them with count_only.

    fields is required (all fields are never read). At most 500 records per call;
    page with offset. company_ids sets allowed_company_ids in multi-company databases.
    Fields that look like they store secrets are rejected with an error.
    """

    async def run():
        client = session.current()
        context = _context(company_ids)
        if count_only:
            count = await client.read(model, "search_count", {"domain": domain or [], "context": context})
            return {"model": model, "count": count}
        params = {"domain": domain or [], "fields": fields, "limit": limit, "offset": offset, "context": context}
        if order:
            params["order"] = order
        records = await client.read(model, "search_read", params)
        result = {"model": model, "count": len(records), "records": records}
        if len(records) == limit:
            result["notice"] = f"There may be more: ask for the next page with offset={offset + limit}."
        return result

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def group_by(
    model: str,
    groupby: list[str],
    aggregates: list[str] | None = None,
    domain: list[Any] | None = None,
    limit: int | None = None,
    order: str | None = None,
    company_ids: list[int] | None = None,
) -> str:
    """Groups and aggregates records (formatted_read_group).

    groupby: "field" or "field:granularity" (day, week, month, quarter, year).
    aggregates: "field:function" (sum, avg, min, max, count_distinct…) or "__count" (default).
    """

    async def run():
        params = {
            "domain": domain or [],
            "groupby": groupby,
            "aggregates": aggregates or ["__count"],
            "context": _context(company_ids),
        }
        if limit:
            params["limit"] = limit
        if order:
            params["order"] = order
        return await session.current().read(model, "formatted_read_group", params)

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def fields(model: str, attributes: list[str] | None = None, field_names: list[str] | None = None) -> str:
    """Field definitions of a model (fields_get), as the ORM sees them in this database.

    Default attributes: string, type, relation, required, readonly, store, selection.
    Add "help" or "compute"… if you need them. field_names limits the result to those fields.
    """

    async def run():
        params = {"attributes": attributes or ["string", "type", "relation", "required", "readonly", "store", "selection"]}
        if field_names:
            params["allfields"] = field_names
        return await session.current().read(model, "fields_get", params)

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def model_info(model: str, name_filter: str | None = None) -> str:
    """Technical sheet of a model: its fields according to ir.model.fields (compute, depends, related,
    store and the modules that define them), its access rights and its record rules.

    name_filter filters the fields by name (ilike) if the model has too many.
    """

    async def run():
        client = session.current()
        field_columns = await _available(
            client,
            "ir.model.fields",
            ["name", "field_description", "ttype", "relation", "required", "readonly", "store",
             "compute", "depends", "related", "modules", "copied", "index"],
        )
        domain = [["model", "=", model]]
        if name_filter:
            domain.append(["name", "ilike", name_filter])
        model_fields = await client.read(
            "ir.model.fields", "search_read", {"domain": domain, "fields": field_columns, "limit": 500, "order": "name"}
        )
        if not model_fields:
            raise OdooError(f"No fields for model {model} in this database (does it exist, is it spelled right?).")
        access_columns = await _available(
            client, "ir.model.access", ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink", "active"]
        )
        rule_columns = await _available(
            client,
            "ir.rule",
            ["name", "groups", "domain_force", "perm_read", "perm_write", "perm_create", "perm_unlink", "global", "active"],
        )
        by_model = [["model_id.model", "=", model]]
        inactive_too = {"active_test": False}
        access = await client.read(
            "ir.model.access", "search_read", {"domain": by_model, "fields": access_columns, "limit": 200, "context": inactive_too}
        )
        rules = await client.read(
            "ir.rule", "search_read", {"domain": by_model, "fields": rule_columns, "limit": 200, "context": inactive_too}
        )
        return {"model": model, "fields": model_fields, "access_rights": access, "record_rules": rules}

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def get_view(model: str, view_type: str = "form", view_id: int | None = None) -> str:
    """Final architecture of a view, with all inheritance applied (get_views), as the key's user
    sees it. Without view_id, the default view of that type.

    To see the views it is built from and their inheritance, read ir.ui.view with search_read.
    """

    async def run():
        result = await session.current().read(model, "get_views", {"views": [[view_id or False, view_type]], "options": {}})
        view = result["views"][view_type]
        return f"<!-- {model} · {view_type} · view id {view.get('id')} -->\n{view['arch']}"

    return await _run(run)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True),
    structured_output=False,
)
async def ask_odoo_ai(
    problem: str,
    steps: list[str],
    evidence: str,
    models: list[str],
    previous_review: str | None = None,
) -> str:
    """Cross-checks a proposal with this same database's AI (an agent of the Odoo AI app).

    problem: what needs solving. steps: the proposed steps, one per item.
    evidence: what was already checked in the database (models, fields, views, counts), without personal data.
    models: the models involved. previous_review: in the second round, the previous answer and what changed.
    If it cannot be queried automatically, returns the text for manual mode (REVIEW PENDING).
    """

    async def run():
        client = session.current()
        if not session.facts:
            await _describe(client)
        return await review.ask(client, session.facts, problem, steps, evidence, models, previous_review)

    return await _run(run)


async def _run(fn) -> str:
    try:
        return guard.render(await fn())
    except (guard.GuardError, OdooError) as e:
        raise ToolError(guard.render(str(e))) from None


def _context(company_ids: list[int] | None) -> dict:
    return {"allowed_company_ids": company_ids} if company_ids else {}


async def _available(client: OdooClient, model: str, wanted: list[str]) -> list[str]:
    """The fields requested from a technical model, intersected with those that exist in this version."""
    existing = await client.read(model, "fields_get", {"attributes": ["type"]})
    return [name for name in wanted if name in existing]


def own_modules_only(modules: list[dict], sources: Path) -> list[dict]:
    """Drops the modules shipped in that branch's Odoo sources (e.g. l10n_mx, by Vauxoo)."""
    roots = [sources / "odoo" / "addons", sources / "odoo" / "odoo" / "addons", sources / "enterprise"]
    return [m for m in modules if not any((root / m["name"] / "__manifest__.py").exists() for root in roots)]


async def _describe(client: OdooClient) -> dict:
    version = (await client.version())["version"]
    branch = source_branch(version)
    sources = SOURCES_ROOT / branch
    installed = await client.read(
        "ir.module.module", "search_read",
        {"domain": [["state", "=", "installed"], ["name", "in", list(KEY_MODULES)]], "fields": ["name"], "limit": 100},
    )
    installed = {row["name"] for row in installed}
    installed_count = await client.read("ir.module.module", "search_count", {"domain": [["state", "=", "installed"]]})
    candidates = await client.read(
        "ir.module.module", "search_read",
        {
            "domain": [["state", "=", "installed"], ["author", "not ilike", "odoo"], ["author", "not ilike", "openerp"]],
            "fields": ["name", "shortdesc", "author", "latest_version"],
            "limit": 300,
        },
    )
    own_modules = own_modules_only(candidates, sources)
    companies = await client.read(
        "res.company", "search_read", {"domain": [], "fields": ["name", "country_id", "currency_id"], "limit": 50}
    )
    ai_installed = "ai" in installed
    facts = {
        "profile": client.profile.name,
        "url": client.profile.url,
        "hosting": client.profile.hosting,
        "environment": client.profile.env,
        "version": version,
        "edition": "enterprise" if "web_enterprise" in installed else "community",
        "sources": {
            "branch": branch,
            "path": str(sources),
            "cloned": sorted(p.name for p in sources.iterdir() if (p / ".git").exists()) if sources.is_dir() else [],
        },
        "installed_modules": installed_count,
        "key_modules": sorted(installed),
        "custom_modules": own_modules,
        "companies": companies,
        "ai_installed": ai_installed,
    }
    if ai_installed:
        try:
            facts["ai_agents"] = [
                {"id": c.id, "name": c.name, "topics": c.topics, "usable": c.usable, "reason": "; ".join(c.problems)}
                for c in await review.check_agents(client)
            ]
        except OdooError as e:
            facts["ai_agents"] = f"Could not be checked: {e}"
    session.facts = facts
    return facts


def main() -> None:
    mcp.run(transport="stdio")
