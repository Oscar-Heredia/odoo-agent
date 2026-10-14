import asyncio

import httpx2
import pytest

from odoo_agent.odoo import OdooError, Session, load_env, load_profiles

ENV = """\
# Test project
ODOO_PROFILE=acme-test
ODOO_URL="https://acme.odoo.com/"
ODOO_HOSTING=online      # online | odoo.sh | onpremise
ODOO_ENV='test'
# ODOO_DB=acme
ODOO_API_KEY=key123
"""


@pytest.fixture(autouse=True)
def no_session_environment(monkeypatch):
    """What the shell function sets must not leak into the tests."""
    for name in ("ODOO_PROFILE", "ODOO_ENV_FILE", "ODOO_PROJECTS_ROOT"):
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, text=ENV):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def make_project(root, name, text=ENV):
    folder = root / name
    folder.mkdir(parents=True)
    return write_env(folder, text)


def projects_session(tmp_path, transport=None):
    """A session opened outside any project and without a profiles.toml, as a subagent from ~ is."""
    return Session(
        load=lambda: load_profiles(tmp_path / "missing.toml"),
        transport=transport,
        projects=tmp_path / "proyectos",
    )


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


def test_a_project_folder_name_pins_its_env(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx2.Response(200, json={"version": "19.0"})

    make_project(tmp_path / "proyectos", "acme")
    session = projects_session(tmp_path, httpx2.MockTransport(handler))
    client = session.bind("acme")
    asyncio.run(client.version())
    # The .env names its profile acme-test; the folder name and the profile name both reach it.
    assert client.profile.name == "acme-test"
    assert seen["auth"] == "bearer key123"
    assert session.bind("acme") is client
    assert session.bind("acme-test") is client


def test_a_pinned_project_rejects_another_one(tmp_path):
    root = tmp_path / "proyectos"
    make_project(root, "acme")
    make_project(root, "beta", ENV.replace("acme-test", "beta"))
    session = projects_session(tmp_path)
    session.bind("acme")
    with pytest.raises(OdooError, match="pinned"):
        session.bind("beta")


@pytest.mark.parametrize("name", ["..", "../outside", "acme/..", ".hidden", "a/b"])
def test_a_project_name_cannot_leave_the_projects_folder(tmp_path, name):
    root = tmp_path / "proyectos"
    make_project(root, "acme")
    make_project(root, ".hidden")
    make_project(tmp_path, "outside")
    with pytest.raises(OdooError, match="No profile or project"):
        projects_session(tmp_path).bind(name)


def test_an_unknown_name_lists_the_projects(tmp_path):
    make_project(tmp_path / "proyectos", "acme")
    with pytest.raises(OdooError, match="Projects: acme"):
        projects_session(tmp_path).bind("nope")


def test_without_profiles_toml_the_choices_are_the_projects(tmp_path):
    root = tmp_path / "proyectos"
    make_project(root, "acme")
    make_project(root, "beta")
    (root / "no-env").mkdir()
    assert projects_session(tmp_path).choices() == {
        "profiles": [],
        "projects": ["acme", "beta"],
        "projects_folder": str(root),
    }


def test_a_profiles_toml_profile_wins_over_a_project_with_its_name(tmp_path):
    toml = tmp_path / "profiles.toml"
    toml.write_text('[profiles.acme]\nurl = "https://toml.example.com"\nhosting = "online"\nenv = "test"\n')
    root = tmp_path / "proyectos"
    make_project(root, "acme")
    session = Session(load=lambda: load_profiles(toml), api_key=lambda _: "toml-key", projects=root)
    assert session.bind("acme").profile.url == "https://toml.example.com"
