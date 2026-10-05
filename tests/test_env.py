import asyncio

import httpx2
import pytest

from odoo_agent.odoo import OdooError, Session, load_env

ENV = """\
# Proyecto de prueba
ODOO_PROFILE=acme-test
ODOO_URL="https://acme.odoo.com/"
ODOO_HOSTING=online      # online | odoo.sh | onpremise
ODOO_ENV='test'
# ODOO_DB=acme
ODOO_API_KEY=clave123
"""


def write_env(tmp_path, text=ENV):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_lee_perfil_y_clave_con_comentarios_y_comillas(tmp_path):
    profile, key = load_env(write_env(tmp_path))
    assert key == "clave123"
    assert profile.name == "acme-test"
    assert profile.url == "https://acme.odoo.com"
    assert profile.hosting == "online"
    assert profile.env == "test"
    assert profile.db is None


def test_sin_nombre_usa_el_de_la_carpeta(tmp_path):
    profile, _ = load_env(write_env(tmp_path, ENV.replace("ODOO_PROFILE=acme-test\n", "")))
    assert profile.name == tmp_path.name


def test_exige_la_clave_api(tmp_path):
    with pytest.raises(OdooError, match="ODOO_API_KEY"):
        load_env(write_env(tmp_path, ENV.replace("clave123", "")))


def test_rechaza_hosting_desconocido(tmp_path):
    with pytest.raises(OdooError, match="ODOO_HOSTING"):
        load_env(write_env(tmp_path, ENV.replace("online", "nube")))


def test_la_sesion_se_fija_al_perfil_del_env(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx2.Response(200, json={"version": "19.0"})

    session = Session(env_path=write_env(tmp_path), transport=httpx2.MockTransport(handler))
    client = session.current()
    asyncio.run(client.version())
    assert client.profile.name == "acme-test"
    assert seen == {"auth": "bearer clave123", "url": "https://acme.odoo.com/json/version"}
    with pytest.raises(OdooError, match="abre otra sesión"):
        session.bind("otro")


def test_un_env_sin_clave_falla_al_usarlo_y_se_puede_corregir(tmp_path):
    path = write_env(tmp_path, ENV.replace("clave123", ""))
    session = Session(env_path=path)
    with pytest.raises(OdooError, match="ODOO_API_KEY"):
        session.current()
    path.write_text(ENV)
    assert session.current().profile.name == "acme-test"
