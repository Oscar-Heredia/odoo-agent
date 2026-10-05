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
def test_rechaza_metodos_fuera_de_la_lista_blanca(method):
    with pytest.raises(GuardError, match=method):
        prepare_read("res.partner", method, {})


def test_search_read_exige_campos():
    with pytest.raises(GuardError, match="fields"):
        prepare_read("res.partner", "search_read", {"domain": []})


def test_search_read_limita_a_50_por_defecto():
    params = prepare_read("res.partner", "search_read", {"domain": [], "fields": ["name"]})
    assert params["fields"] == ["name"]
    assert params["limit"] == 50


def test_search_read_rechaza_mas_de_500_registros():
    with pytest.raises(GuardError, match="500"):
        prepare_read("res.partner", "search_read", {"fields": ["name"], "limit": 501})


@pytest.mark.parametrize(
    "model",
    ["res.users.apikeys", "res.users.apikeys.description", "auth_totp.device", "res.users.identitycheck"],
)
def test_rechaza_modelos_con_credenciales(model):
    with pytest.raises(GuardError, match=model):
        prepare_read(model, "search_read", {"fields": ["name"]})


@pytest.mark.parametrize(
    "field",
    ["password", "api_key_ids", "access_token", "smtp_pass", "totp_secret", "client_secret", "pin", "stripe_publishable_key"],
)
def test_rechaza_campos_sensibles_en_fields(field):
    with pytest.raises(GuardError, match=field):
        prepare_read("res.users", "search_read", {"fields": ["name", field]})


def test_no_confunde_key_con_un_campo_sensible():
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
def test_rechaza_campos_sensibles_en_el_dominio(domain):
    with pytest.raises(GuardError, match="secreto"):
        prepare_read("res.partner", "search_count", {"domain": domain})


def test_acepta_dominios_normales():
    domain = ["|", ["name", "ilike", "acme"], ["user_ids", "any", [["login", "=", "x"]]], [1, "=", 1]]
    params = prepare_read("res.partner", "search_count", {"domain": domain})
    assert params["domain"] == domain


def test_rechaza_campos_sensibles_en_order():
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
def test_rechaza_campos_sensibles_al_agrupar(key, value):
    with pytest.raises(GuardError, match="secreto"):
        prepare_read("res.partner", "formatted_read_group", {"groupby": ["country_id"], key: value})


def test_acepta_agrupaciones_normales():
    params = prepare_read(
        "res.partner",
        "formatted_read_group",
        {"groupby": ["country_id", "create_date:month"], "aggregates": ["__count", "credit:sum"]},
    )
    assert params["aggregates"] == ["__count", "credit:sum"]


@pytest.mark.parametrize("method", ["search_read", "search_count", "formatted_read_group"])
def test_parametros_del_sistema_solo_con_claves_permitidas(method):
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


def test_el_contexto_solo_admite_claves_conocidas():
    with pytest.raises(GuardError, match="force_company"):
        prepare_read("res.partner", "search_count", {"domain": [], "context": {"force_company": 2}})


def test_el_contexto_siempre_pide_tamanos_en_vez_de_binarios():
    params = prepare_read(
        "res.partner",
        "search_read",
        {"fields": ["name"], "context": {"lang": "es_ES", "allowed_company_ids": [1, 2]}},
    )
    assert params["context"] == {"lang": "es_ES", "allowed_company_ids": [1, 2], "bin_size": True}


def test_sin_contexto_tambien_pide_tamanos():
    params = prepare_read("res.partner", "search_count", {"domain": []})
    assert params["context"] == {"bin_size": True}


def test_render_devuelve_json_compacto():
    assert render({"nombre": "Pérez", "ids": [1, 2]}) == '{"nombre":"Pérez","ids":[1,2]}'


def test_render_deja_el_texto_tal_cual():
    assert render('<form string="Pedido"/>') == '<form string="Pedido"/>'


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
        ("password: 'Sup3rS3creta'", "Sup3rS3creta"),
    ],
)
def test_render_oculta_secretos_dentro_de_los_resultados(text, secret):
    out = render({"records": [{"code": text}]})
    assert secret not in out
    assert "[OCULTO]" in out


def test_render_oculta_secretos_en_texto():
    out = render("Authorization: Bearer abcdef0123456789abcdef")
    assert out == "Authorization: Bearer [OCULTO]"


def test_render_acepta_hasta_60000_caracteres():
    assert len(render("x" * 60_000)) == 60_000


def test_render_rechaza_respuestas_demasiado_grandes():
    with pytest.raises(GuardError, match="limit"):
        render({"records": [{"name": "x" * 1000}] * 61})
