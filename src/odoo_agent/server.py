"""Servidor MCP de odoo-agent: lectura de una base Odoo y cotejo con su IA.

Una sesión trabaja con una sola base. Todas las lecturas pasan por guard
(lista blanca) y todo lo que vuelve al agente pasa por guard.render.
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

# Una línea por petición HTTP solo es ruido en el log de MCP.
logging.getLogger("httpx2").setLevel(logging.WARNING)

mcp = MCPServer(
    "odoo",
    log_level="WARNING",
    instructions=(
        "Lectura de una base Odoo 19+ por JSON-2, siempre en solo lectura, y cotejo con la IA de esa misma base. "
        "Una sesión, una base: empieza llamando a server_info con el perfil."
    ),
)
session = Session()


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def server_info(profile: str | None = None) -> str:
    """Fija la base de esta sesión y la resume. Sin perfil, lista los perfiles disponibles.

    Devuelve versión, edición, hosting y entorno, módulos clave y propios (no de Odoo S.A.),
    compañías, agentes de la app IA (y si se pueden consultar) y dónde están las fuentes
    de esa versión. Una vez fijada, la sesión no puede cambiar de base.
    """

    async def run():
        if profile is None and session.client is None and not session.preset:
            return {
                "perfiles": [
                    {"nombre": p.name, "url": p.url, "hosting": p.hosting, "env": p.env}
                    for p in session.profiles().values()
                ],
                "siguiente": "Llama a server_info con el perfil de la base con la que vas a trabajar.",
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
    """Lee registros de un modelo, o solo los cuenta con count_only.

    fields es obligatorio (nunca se leen todos los campos). Máximo 500 registros por llamada;
    pagina con offset. company_ids fija allowed_company_ids en bases multicompañía.
    Los campos que parecen guardar secretos se rechazan con un error.
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
        result = {"model": model, "registros": len(records), "records": records}
        if len(records) == limit:
            result["aviso"] = f"Puede haber más: pide la página siguiente con offset={offset + limit}."
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
    """Agrupa y agrega registros (formatted_read_group).

    groupby: "campo" o "campo:granularidad" (day, week, month, quarter, year).
    aggregates: "campo:función" (sum, avg, min, max, count_distinct…) o "__count" (por defecto).
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
    """Definición de los campos de un modelo (fields_get), tal como la ve el ORM en esta base.

    attributes por defecto: string, type, relation, required, readonly, store, selection.
    Añade "help" o "compute"… si los necesitas. field_names limita a esos campos.
    """

    async def run():
        params = {"attributes": attributes or ["string", "type", "relation", "required", "readonly", "store", "selection"]}
        if field_names:
            params["allfields"] = field_names
        return await session.current().read(model, "fields_get", params)

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def model_info(model: str, name_filter: str | None = None) -> str:
    """Ficha técnica de un modelo: sus campos según ir.model.fields (compute, depends, related,
    store y módulos que los definen), sus permisos de acceso y sus reglas de registro.

    name_filter filtra los campos por nombre (ilike) si el modelo tiene demasiados.
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
            raise OdooError(f"No hay campos para el modelo {model} en esta base (¿existe y está bien escrito?).")
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
        return {"model": model, "campos": model_fields, "permisos_de_acceso": access, "reglas_de_registro": rules}

    return await _run(run)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
async def get_view(model: str, view_type: str = "form", view_id: int | None = None) -> str:
    """Arquitectura final de una vista, con toda la herencia aplicada (get_views), tal como la ve
    el usuario de la clave. Sin view_id, la vista por defecto de ese tipo.

    Para ver las vistas que la componen y su herencia, lee ir.ui.view con search_read.
    """

    async def run():
        result = await session.current().read(model, "get_views", {"views": [[view_id or False, view_type]], "options": {}})
        view = result["views"][view_type]
        return f"<!-- {model} · {view_type} · vista id {view.get('id')} -->\n{view['arch']}"

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
    """Coteja una propuesta con la IA de esta misma base (agente de la app IA de Odoo).

    problem: qué hay que resolver. steps: los pasos propuestos, uno por elemento.
    evidence: lo ya comprobado en la base (modelos, campos, vistas, conteos), sin datos personales.
    models: modelos implicados. previous_review: en la segunda ronda, la respuesta anterior y lo cambiado.
    Si no se puede consultar automáticamente, devuelve el texto para el modo manual (PENDIENTE DE COTEJO).
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
    """Los campos que se piden a un modelo técnico, cruzados con los que existen en esta versión."""
    existing = await client.read(model, "fields_get", {"attributes": ["type"]})
    return [name for name in wanted if name in existing]


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
    own_modules = await client.read(
        "ir.module.module", "search_read",
        {
            "domain": [["state", "=", "installed"], ["author", "not ilike", "Odoo S.A."]],
            "fields": ["name", "shortdesc", "author", "latest_version"],
            "limit": 300,
        },
    )
    companies = await client.read(
        "res.company", "search_read", {"domain": [], "fields": ["name", "country_id", "currency_id"], "limit": 50}
    )
    ai_installed = "ai" in installed
    facts = {
        "perfil": client.profile.name,
        "url": client.profile.url,
        "hosting": client.profile.hosting,
        "entorno": client.profile.env,
        "version": version,
        "edicion": "enterprise" if "web_enterprise" in installed else "community",
        "fuentes": {
            "rama": branch,
            "ruta": str(sources),
            "clonadas": sorted(p.name for p in sources.iterdir() if (p / ".git").exists()) if sources.is_dir() else [],
        },
        "modulos_instalados": installed_count,
        "modulos_clave": sorted(installed),
        "modulos_propios": own_modules,
        "companias": companies,
        "ai_installed": ai_installed,
    }
    if ai_installed:
        try:
            facts["agentes_ia"] = [
                {"id": c.id, "nombre": c.name, "temas": c.topics, "consultable": c.usable, "motivo": "; ".join(c.problems)}
                for c in await review.check_agents(client)
            ]
        except OdooError as e:
            facts["agentes_ia"] = f"No se pudieron revisar: {e}"
    session.facts = facts
    return facts


def main() -> None:
    mcp.run(transport="stdio")
