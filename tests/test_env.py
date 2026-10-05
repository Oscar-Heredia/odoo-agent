import asyncio

import httpx2
import pytest

from odoo_agent.odoo import OdooError, Session, load_env

ENV = """\
# Test project
ODOO_PROFILE=acme-test
ODOO_URL="https://acme.odoo.com/"
ODOO_HOSTING=online      # online | odoo.sh | onpremise
ODOO_ENV='test'
# ODOO_DB=acme
ODOO_API_KEY=key123
"""


def write_env(tmp_path, text=ENV):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_reads_profile_and_key_with_comments_and_quotes(tmp_path):
    profile, key = load_env(write_env(tmp_path))
    assert key == "key123"
    assert profile.name == "acme-test"
    assert profile.url == "https://acme.odoo.com"
    assert profile.hosting == "online"
    assert profile.env == "test"
    assert profile.db is None


def test_without_a_name_uses_the_folder_name(tmp_path):
    profile, _ = load_env(write_env(tmp_path, ENV.replace("ODOO_PROFILE=acme-test\n", "")))
    assert profile.name == tmp_path.name


def test_requires_the_api_key(tmp_path):
    with pytest.raises(OdooError, match="ODOO_API_KEY"):
        load_env(write_env(tmp_path, ENV.replace("key123", "")))


def test_rejects_unknown_hosting(tmp_path):
    with pytest.raises(OdooError, match="ODOO_HOSTING"):
        load_env(write_env(tmp_path, ENV.replace("online", "cloud")))


def test_the_session_is_pinned_to_the_env_profile(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx2.Response(200, json={"version": "19.0"})

    session = Session(env_path=write_env(tmp_path), transport=httpx2.MockTransport(handler))
    client = session.current()
    asyncio.run(client.version())
    assert client.profile.name == "acme-test"
    assert seen == {"auth": "bearer key123", "url": "https://acme.odoo.com/json/version"}
    with pytest.raises(OdooError, match="open another session"):
        session.bind("other")


def test_an_env_without_a_key_fails_on_use_and_can_be_fixed(tmp_path):
    path = write_env(tmp_path, ENV.replace("key123", ""))
    session = Session(env_path=path)
    with pytest.raises(OdooError, match="ODOO_API_KEY"):
        session.current()
    path.write_text(ENV)
    assert session.current().profile.name == "acme-test"
