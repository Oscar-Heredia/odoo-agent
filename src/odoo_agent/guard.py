"""Lista blanca de lectura: lo único que separa al agente de escribir en una base.

Odoo no puede garantizar el solo lectura (la clave es de administrador y no
admite alcance de lectura), así que toda llamada de lectura pasa por
`prepare_read` y todo lo que vuelve al agente pasa por `render`.
"""

import json
import re

READ_METHODS = frozenset(
    {"search_read", "search_count", "fields_get", "formatted_read_group", "get_views"}
)
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
# Por debajo de los ~25 000 tokens a partir de los cuales Claude Code guarda la
# salida de una herramienta MCP en un archivo en vez de dársela al modelo.
MAX_OUTPUT_CHARS = 60_000
CONTEXT_KEYS = frozenset({"lang", "tz", "allowed_company_ids", "active_test"})
# Modelos que guardan credenciales; se bloquean por prefijo.
BLOCKED_MODEL_PREFIXES = ("res.users.apikeys", "auth_totp", "auth.totp", "res.users.identitycheck")
CONFIG_PARAMETER_MODEL = "ir.config_parameter"
# ir.config_parameter guarda claves (ai.openai_key, database.secret…) en un
# campo `value` cuyo nombre no delata nada; solo se leen estas claves.
ALLOWED_CONFIG_PARAMETERS = (
    "auth_signup.allow_uninvited",
    "auth_signup.invitation_scope",
    "auth_signup.reset_password",
    "base_setup.default_user_rights",
    "database.create_date",
    "database.expiration_date",
    "database.expiration_reason",
    "database.uuid",
    "mail.bounce.alias",
    "mail.catchall.alias",
    "mail.catchall.domain",
    "mail.default.from",
    "mail.default.from_filter",
    "report.url",
    "web.base.url",
    "web.base.url.freeze",
)
MASK = "[OCULTO]"
# Valores con pinta de secreto que pueden aparecer en cualquier texto: código de
# acciones de servidor, URLs del chatter, respuestas de la IA…
SECRET_VALUES = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), MASK),  # OpenAI
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"), MASK),  # Stripe
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), MASK),  # Google
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),  # AWS
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), MASK),  # GitHub
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),  # Slack
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),  # JWT
    (re.compile(r"(access_token=)[^&\s\"'<>]+"), rf"\1{MASK}"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), rf"\1{MASK}"),
    # Asignaciones en código o config: api_key = "…", 'password': '…'
    (
        re.compile(r"(?i)((?:password|passwd|pwd|secret|api_?key|token)\w*[\"']?\s*[:=]\s*)([\"'])[^\"'\s]{4,}\2"),
        rf"\1\2{MASK}\2",
    ),
)
# Nombres de campo que suelen guardar secretos. `key` a secas no entra: es la
# clave técnica de vistas y parámetros.
SECRET_FIELD = re.compile(
    r"password|passwd|secret|token|api_?key|private_?key|access_key|_key$|smtp_pass|totp|^pin$|credential",
    re.IGNORECASE,
)


class GuardError(Exception):
    """Llamada rechazada; el mensaje se le muestra tal cual al agente."""


def prepare_read(model: str, method: str, params: dict) -> dict:
    if method not in READ_METHODS:
        raise GuardError(
            f"Método no permitido: {method}. Solo se permiten métodos de lectura: "
            f"{', '.join(sorted(READ_METHODS))}."
        )
    if model.startswith(BLOCKED_MODEL_PREFIXES):
        raise GuardError(f"El modelo {model} guarda credenciales y no se puede leer.")
    params = dict(params)
    context = dict(params.get("context") or {})
    unknown = sorted(set(context) - CONTEXT_KEYS)
    if unknown:
        raise GuardError(
            f"Claves de contexto no permitidas: {', '.join(unknown)}. "
            f"Solo se admiten: {', '.join(sorted(CONTEXT_KEYS))}."
        )
    # Con bin_size Odoo devuelve el tamaño de los binarios en vez de su contenido.
    params["context"] = {**context, "bin_size": True}
    for field in params.get("fields") or ():
        _check_field_path(field)
    _check_domain(params.get("domain") or [])
    for term in (params.get("order") or "").split(","):
        if term.strip():
            _check_field_path(term.split()[0])
    # groupby: "campo" o "campo:granularidad"; aggregates: "campo:función" o "__count".
    for spec in [*(params.get("groupby") or ()), *(params.get("aggregates") or ())]:
        if spec != "__count":
            _check_field_path(spec.split(":")[0])
    if model == CONFIG_PARAMETER_MODEL and method not in ("fields_get", "get_views"):
        params["domain"] = [["key", "in", list(ALLOWED_CONFIG_PARAMETERS)], *(params.get("domain") or [])]
    if method == "search_read":
        if not params.get("fields"):
            raise GuardError(
                "Indica los campos que necesitas en fields; leer todos los campos "
                "trae binarios y datos de más."
            )
        params.setdefault("limit", DEFAULT_LIMIT)
    if (params.get("limit") or 0) > MAX_LIMIT:
        raise GuardError(f"limit máximo: {MAX_LIMIT}. Pagina con offset.")
    return params


def render(result) -> str:
    """Texto que recibe el agente: el resultado de Odoo, sin secretos y acotado."""
    result = _scrub(result)
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        raise GuardError(
            f"La respuesta ocupa {len(text)} caracteres (máximo {MAX_OUTPUT_CHARS}). "
            "Pide menos campos, baja limit y pagina con offset, o filtra más el dominio."
        )
    return text


def _check_field_path(path: str) -> None:
    for name in path.split("."):
        if SECRET_FIELD.search(name):
            raise GuardError(
                f"El campo {path} parece guardar un secreto y no se puede leer ni usar "
                "en filtros, orden o agrupaciones."
            )


def _check_domain(domain: list) -> None:
    for term in domain:
        # Los operadores "&", "|" y "!" son cadenas; las hojas, listas [campo, operador, valor].
        if not isinstance(term, (list, tuple)) or len(term) != 3:
            continue
        path, operator, value = term
        if not isinstance(path, str):  # hojas constantes como [1, "=", 1]
            continue
        _check_field_path(path)
        if operator in ("any", "not any") and isinstance(value, list):
            _check_domain(value)


def _scrub(value):
    if isinstance(value, str):
        for pattern, replacement in SECRET_VALUES:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value
