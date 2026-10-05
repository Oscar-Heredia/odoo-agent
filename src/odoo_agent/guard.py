"""Read whitelist: the only thing standing between the agent and writing to a database.

Odoo cannot enforce read-only access (the key belongs to an administrator and has
no read-only scope), so every read call goes through `prepare_read` and everything
returned to the agent goes through `render`.
"""

import json
import re

READ_METHODS = frozenset(
    {"search_read", "search_count", "fields_get", "formatted_read_group", "get_views"}
)
DEFAULT_LIMIT = 50
MAX_LIMIT = 500
# Below the ~25,000 tokens above which Claude Code saves an MCP tool's output to a
# file instead of handing it to the model.
MAX_OUTPUT_CHARS = 60_000
CONTEXT_KEYS = frozenset({"lang", "tz", "allowed_company_ids", "active_test"})
# Models that store credentials; blocked by prefix.
BLOCKED_MODEL_PREFIXES = ("res.users.apikeys", "auth_totp", "auth.totp", "res.users.identitycheck")
CONFIG_PARAMETER_MODEL = "ir.config_parameter"
# ir.config_parameter stores keys (ai.openai_key, database.secret…) in a `value`
# field whose name gives nothing away; only these keys are read.
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
MASK = "[HIDDEN]"
# Secret-looking values that can show up in any text: server action code, chatter
# URLs, AI replies…
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
    # Assignments in code or config: api_key = "…", 'password': '…'
    (
        re.compile(r"(?i)((?:password|passwd|pwd|secret|api_?key|token)\w*[\"']?\s*[:=]\s*)([\"'])[^\"'\s]{4,}\2"),
        rf"\1\2{MASK}\2",
    ),
)
# Field names that usually hold secrets. A bare `key` is not included: it is the
# technical key of views and parameters.
SECRET_FIELD = re.compile(
    r"password|passwd|secret|token|api_?key|private_?key|access_key|_key$|smtp_pass|totp|^pin$|credential",
    re.IGNORECASE,
)


class GuardError(Exception):
    """Rejected call; the message is shown to the agent as is."""


def prepare_read(model: str, method: str, params: dict) -> dict:
    if method not in READ_METHODS:
        raise GuardError(
            f"Method not allowed: {method}. Only read methods are allowed: "
            f"{', '.join(sorted(READ_METHODS))}."
        )
    if model.startswith(BLOCKED_MODEL_PREFIXES):
        raise GuardError(f"Model {model} stores credentials and cannot be read.")
    params = dict(params)
    context = dict(params.get("context") or {})
    unknown = sorted(set(context) - CONTEXT_KEYS)
    if unknown:
        raise GuardError(
            f"Context keys not allowed: {', '.join(unknown)}. "
            f"Only these are accepted: {', '.join(sorted(CONTEXT_KEYS))}."
        )
    # With bin_size Odoo returns the size of binaries instead of their content.
    params["context"] = {**context, "bin_size": True}
    for field in params.get("fields") or ():
        _check_field_path(field)
    _check_domain(params.get("domain") or [])
    for term in (params.get("order") or "").split(","):
        if term.strip():
            _check_field_path(term.split()[0])
    # groupby: "field" or "field:granularity"; aggregates: "field:function" or "__count".
    for spec in [*(params.get("groupby") or ()), *(params.get("aggregates") or ())]:
        if spec != "__count":
            _check_field_path(spec.split(":")[0])
    if model == CONFIG_PARAMETER_MODEL and method not in ("fields_get", "get_views"):
        params["domain"] = [["key", "in", list(ALLOWED_CONFIG_PARAMETERS)], *(params.get("domain") or [])]
    if method == "search_read":
        if not params.get("fields"):
            raise GuardError(
                "List the fields you need in fields; reading every field brings "
                "binaries and too much data."
            )
        params.setdefault("limit", DEFAULT_LIMIT)
    if (params.get("limit") or 0) > MAX_LIMIT:
        raise GuardError(f"Maximum limit: {MAX_LIMIT}. Page with offset.")
    return params


def render(result) -> str:
    """Text the agent receives: Odoo's result, without secrets and size-capped."""
    result = _scrub(result)
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        raise GuardError(
            f"The response is {len(text)} characters long (maximum {MAX_OUTPUT_CHARS}). "
            "Ask for fewer fields, lower limit and page with offset, or narrow the domain."
        )
    return text


def _check_field_path(path: str) -> None:
    for name in path.split("."):
        if SECRET_FIELD.search(name):
            raise GuardError(
                f"Field {path} looks like it stores a secret and cannot be read or used "
                "in filters, ordering or grouping."
            )


def _check_domain(domain: list) -> None:
    for term in domain:
        # The "&", "|" and "!" operators are strings; leaves are [field, operator, value] lists.
        if not isinstance(term, (list, tuple)) or len(term) != 3:
            continue
        path, operator, value = term
        if not isinstance(path, str):  # constant leaves such as [1, "=", 1]
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
