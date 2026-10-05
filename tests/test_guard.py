import pytest

from odoo_agent.guard import GuardError, prepare_read, render


@pytest.mark.parametrize(
    "method",
    [
        "write",
        "create",
        "unlink",
        "copy",
        "action_confirm",
        "button_validate",
        "message_post",
        "run",
        "get_direct_response",
    ],
)
def test_rejects_methods_outside_the_whitelist(method):
    with pytest.raises(GuardError, match=method):
        prepare_read("res.partner", method, {})


def test_search_read_requires_fields():
    with pytest.raises(GuardError, match="fields"):
        prepare_read("res.partner", "search_read", {"domain": []})


def test_search_read_limits_to_50_by_default():
    params = prepare_read("res.partner", "search_read", {"domain": [], "fields": ["name"]})
    assert params["fields"] == ["name"]
    assert params["limit"] == 50


def test_search_read_rejects_more_than_500_records():
    with pytest.raises(GuardError, match="500"):
        prepare_read("res.partner", "search_read", {"fields": ["name"], "limit": 501})


@pytest.mark.parametrize(
    "model",
    ["res.users.apikeys", "res.users.apikeys.description", "auth_totp.device", "res.users.identitycheck"],
)
def test_rejects_models_with_credentials(model):
    with pytest.raises(GuardError, match=model):
        prepare_read(model, "search_read", {"fields": ["name"]})


@pytest.mark.parametrize(
    "field",
    ["password", "api_key_ids", "access_token", "smtp_pass", "totp_secret", "client_secret", "pin", "stripe_publishable_key"],
)
def test_rejects_sensitive_fields_in_fields(field):
    with pytest.raises(GuardError, match=field):
        prepare_read("res.users", "search_read", {"fields": ["name", field]})


def test_does_not_mistake_key_for_a_sensitive_field():
    params = prepare_read("ir.ui.view", "search_read", {"fields": ["name", "key", "arch_db"]})
    assert params["fields"] == ["name", "key", "arch_db"]


@pytest.mark.parametrize(
    "domain",
    [
        [["password", "!=", False]],
        [["user_ids.password", "=like", "a%"]],
        ["|", ["name", "=", "x"], ["access_token", "!=", False]],
        [["user_ids", "any", [["api_key_ids", "!=", False]]]],
        ["!", ["user_ids", "not any", [["totp_secret", "=", False]]]],
    ],
)
def test_rejects_sensitive_fields_in_the_domain(domain):
    with pytest.raises(GuardError, match="secret"):
        prepare_read("res.partner", "search_count", {"domain": domain})


def test_accepts_normal_domains():
    domain = ["|", ["name", "ilike", "acme"], ["user_ids", "any", [["login", "=", "x"]]], [1, "=", 1]]
    params = prepare_read("res.partner", "search_count", {"domain": domain})
    assert params["domain"] == domain


def test_rejects_sensitive_fields_in_order():
    with pytest.raises(GuardError, match="signup_token"):
        prepare_read("res.partner", "search_read", {"fields": ["name"], "order": "name, signup_token desc"})


@pytest.mark.parametrize(
    "key, value",
    [
        ("groupby", ["user_ids.password"]),
        ("groupby", ["access_token:day"]),
        ("aggregates", ["client_secret:count_distinct"]),
    ],
)
def test_rejects_sensitive_fields_when_grouping(key, value):
    with pytest.raises(GuardError, match="secret"):
        prepare_read("res.partner", "formatted_read_group", {"groupby": ["country_id"], key: value})


def test_accepts_normal_groupings():
    params = prepare_read(
        "res.partner",
        "formatted_read_group",
        {"groupby": ["country_id", "create_date:month"], "aggregates": ["__count", "credit:sum"]},
    )
    assert params["aggregates"] == ["__count", "credit:sum"]


@pytest.mark.parametrize("method", ["search_read", "search_count", "formatted_read_group"])
def test_system_parameters_only_with_allowed_keys(method):
    params = prepare_read(
        "ir.config_parameter",
        method,
        {"fields": ["key", "value"], "groupby": ["key"], "domain": [["key", "ilike", "mail"]]},
    )
    field, operator, allowed = params["domain"][0]
    assert (field, operator) == ("key", "in")
    assert "web.base.url" in allowed
    assert "database.secret" not in allowed
    assert "ai.openai_key" not in allowed
    assert params["domain"][1:] == [["key", "ilike", "mail"]]


def test_context_only_accepts_known_keys():
    with pytest.raises(GuardError, match="force_company"):
        prepare_read("res.partner", "search_count", {"domain": [], "context": {"force_company": 2}})


def test_context_always_asks_for_sizes_instead_of_binaries():
    params = prepare_read(
        "res.partner",
        "search_read",
        {"fields": ["name"], "context": {"lang": "es_ES", "allowed_company_ids": [1, 2]}},
    )
    assert params["context"] == {"lang": "es_ES", "allowed_company_ids": [1, 2], "bin_size": True}


def test_without_context_it_also_asks_for_sizes():
    params = prepare_read("res.partner", "search_count", {"domain": []})
    assert params["context"] == {"bin_size": True}


def test_render_returns_compact_json():
    assert render({"name": "Pérez", "ids": [1, 2]}) == '{"name":"Pérez","ids":[1,2]}'


def test_render_leaves_text_as_is():
    assert render('<form string="Order"/>') == '<form string="Order"/>'


@pytest.mark.parametrize(
    "text, secret",
    [
        ("openai = sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234567890", "AbCdEfGhIjKlMnOpQrStUvWx1234567890"),
        ("stripe: sk_live_51HxYzAbCdEfGhIjKlMnOp", "51HxYzAbCdEfGhIjKlMnOp"),
        ("gemini AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
        ("aws AKIAIOSFODNN7EXAMPLE", "IOSFODNN7EXAMPLE"),
        (
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----",
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
        ),
        (
            "https://acme.odoo.com/my/orders/7?access_token=4f9c2e1a-77b2-4c1e-9d1f-0a1b2c3d4e5f&x=1",
            "4f9c2e1a-77b2-4c1e-9d1f-0a1b2c3d4e5f",
        ),
        ("Authorization: Bearer abcdef0123456789abcdef", "abcdef0123456789abcdef"),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ),
        ('headers = {"x": 1}\napi_key = "abcd1234efgh"', "abcd1234efgh"),
        ("password: 'Sup3rS3cret'", "Sup3rS3cret"),
    ],
)
def test_render_hides_secrets_inside_results(text, secret):
    out = render({"records": [{"code": text}]})
    assert secret not in out
    assert "[HIDDEN]" in out


def test_render_hides_secrets_in_text():
    out = render("Authorization: Bearer abcdef0123456789abcdef")
    assert out == "Authorization: Bearer [HIDDEN]"


def test_render_accepts_up_to_60000_characters():
    assert len(render("x" * 60_000)) == 60_000


def test_render_rejects_responses_that_are_too_large():
    with pytest.raises(GuardError, match="limit"):
        render({"records": [{"name": "x" * 1000}] * 61})
