"""Connection to an Odoo database over JSON-2: profiles, keyring key and client.

A session works with a single database: the profile from ODOO_PROFILE, the one
from the project's .env, or the first one used, stays pinned. That way one
client's data never ends up in another client's database or AI.
"""

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

from . import guard

HOSTINGS = ("online", "odoo.sh", "onpremise")
ENVIRONMENTS = ("prod", "staging", "test")
KEYRING_SERVICE = "odoo-agent"
READ_TIMEOUT = 30.0
ERROR_PREFIXES = {
    403: "Odoo denied access",
    404: "Not found in this database",
    409: "Lock conflict in Odoo; retry",
    422: "Odoo rejected the call",
}
NEW_API_KEY = "Preferences → Account Security → New API Key"


class OdooError(Exception):
    """Odoo or connection failure, with a message written for the agent."""


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    hosting: str
    env: str
    db: str | None = None
    ai_agent: str | None = None


def profiles_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home) / "odoo-agent" / "profiles.toml"


def load_profiles(path: Path | None = None) -> dict[str, Profile]:
    path = path or profiles_path()
    if not path.exists():
        raise OdooError(f"{path} does not exist. Create it from profiles.example.toml in the odoo-agent repo.")
    with path.open("rb") as f:
        data = tomllib.load(f)
    profiles = {}
    for name, raw in data.get("profiles", {}).items():
        missing = [key for key in ("url", "hosting", "env") if key not in raw]
        if missing:
            raise OdooError(f"Profile {name} is missing {', '.join(missing)} in {path}.")
        if raw["hosting"] not in HOSTINGS:
            raise OdooError(f"Profile {name}: hosting must be one of {', '.join(HOSTINGS)}.")
        if raw["env"] not in ENVIRONMENTS:
            raise OdooError(f"Profile {name}: env must be one of {', '.join(ENVIRONMENTS)}.")
        profiles[name] = Profile(
            name=name,
            url=raw["url"].rstrip("/"),
            hosting=raw["hosting"],
            env=raw["env"],
            db=raw.get("db"),
            ai_agent=raw.get("ai_agent"),
        )
    return profiles


def keyring_api_key(profile: Profile) -> str:
    """The profile's API key, read from GNOME Keyring. Never stored on disk."""
    store = f'secret-tool store --label "odoo-agent: {profile.name}" service {KEYRING_SERVICE} profile {profile.name}'
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", KEYRING_SERVICE, "profile", profile.name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OdooError(f"Could not read the keyring: {e}") from None
    key = result.stdout.strip()
    if result.returncode != 0 or not key:
        # secret-tool prints nothing when the key does not exist; any output means the keyring failed.
        detail = f" (secret-tool: {result.stderr.strip()})" if result.stderr.strip() else ""
        raise OdooError(
            f"No API key for profile {profile.name} in the keyring{detail}. "
            f"Store it from a terminal with: {store}"
        )
    return key


def env_file() -> Path | None:
    """The project's .env: the one in ODOO_ENV_FILE or, failing that, the one in the current directory."""
    explicit = os.environ.get("ODOO_ENV_FILE")
    if explicit:
        return Path(explicit)
    path = Path.cwd() / ".env"
    return path if path.exists() else None


def load_env(path: Path) -> tuple[Profile, str]:
    """Profile and API key from a project .env (KEY=VALUE, with # comments and quotes)."""
    if not path.exists():
        raise OdooError(f"{path} does not exist.")
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) > 1 and value[0] in "\"'" and value.endswith(value[0]):
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0]
        values[key.strip()] = value
    missing = [key for key in ("ODOO_URL", "ODOO_HOSTING", "ODOO_ENV") if not values.get(key)]
    if missing:
        raise OdooError(f"{', '.join(missing)} missing in {path}.")
    if values["ODOO_HOSTING"] not in HOSTINGS:
        raise OdooError(f"{path}: ODOO_HOSTING must be one of {', '.join(HOSTINGS)}.")
    if values["ODOO_ENV"] not in ENVIRONMENTS:
        raise OdooError(f"{path}: ODOO_ENV must be one of {', '.join(ENVIRONMENTS)}.")
    key = values.get("ODOO_API_KEY")
    if not key:
        raise OdooError(
            f"No API key in {path}. Create one in Odoo ({NEW_API_KEY}) and paste it after ODOO_API_KEY=."
        )
    profile = Profile(
        name=values.get("ODOO_PROFILE") or path.resolve().parent.name,
        url=values["ODOO_URL"].rstrip("/"),
        hosting=values["ODOO_HOSTING"],
        env=values["ODOO_ENV"],
        db=values.get("ODOO_DB") or None,
        ai_agent=values.get("ODOO_AI_AGENT") or None,
    )
    return profile, key


def source_branch(version: str) -> str:
    """Source branch for a server version: 'saas~19.2+e' → 'saas-19.2'."""
    match = re.match(r"(saas~)?(\d+)\.(\d+)", version)
    if not match:
        raise OdooError(f"Unrecognized Odoo version: {version}")
    saas, major, minor = match.groups()
    return f"{'saas-' if saas else ''}{major}.{minor}"


class OdooClient:
    def __init__(self, profile: Profile, api_key: str, transport: httpx2.AsyncBaseTransport | None = None):
        headers = {"Authorization": f"bearer {api_key}", "User-Agent": "odoo-agent"}
        if profile.db:
            headers["X-Odoo-Database"] = profile.db
        self.profile = profile
        self._http = httpx2.AsyncClient(
            base_url=profile.url, headers=headers, timeout=READ_TIMEOUT, transport=transport
        )

    async def version(self) -> dict:
        try:
            response = await self._http.get("/json/version")
        except httpx2.HTTPError as e:
            raise OdooError(f"Could not connect to {self.profile.url}: {e}") from None
        if response.status_code != 200:
            raise _odoo_error(response, self.profile)
        return response.json()

    async def read(self, model: str, method: str, params: dict) -> Any:
        """The only read path: everything goes through the guard's whitelist."""
        return await self._call(model, method, guard.prepare_read(model, method, params))

    async def direct_response(self, agent_id: int, prompt: str, context_message: str, timeout: float) -> Any:
        """The only call that is not a pure read: asks an agent of the AI app for its opinion."""
        payload = {
            "ids": [agent_id],
            "prompt": prompt,
            "context_message": context_message,
            "enable_html_response": False,
        }
        return await self._call("ai.agent", "get_direct_response", payload, timeout)

    async def _call(self, model: str, method: str, payload: dict, timeout: float = READ_TIMEOUT) -> Any:
        try:
            response = await self._http.post(f"/json/2/{model}/{method}", json=payload, timeout=timeout)
        except httpx2.TimeoutException:
            raise OdooError(f"Odoo did not answer {model}.{method} within {timeout:.0f} s.") from None
        except httpx2.HTTPError as e:
            raise OdooError(f"Could not connect to {self.profile.url}: {e}") from None
        if response.status_code != 200:
            raise _odoo_error(response, self.profile)
        return response.json()


class Session:
    """One session, one database."""

    def __init__(
        self, load=None, api_key=None, transport=None, preset: str | None = None, env_path: Path | None = None
    ):
        self.preset = preset if preset is not None else os.environ.get("ODOO_PROFILE") or None
        if load is None and api_key is None and self.preset is None:
            env_path = env_path or env_file()
        # Project with a .env: its profile is the session's only one. It is read on use,
        # so a .env without a key does not bring the server down; fill it in and retry.
        self.env_path = env_path
        self._load = load or load_profiles
        self._api_key = api_key or keyring_api_key
        self._transport = transport
        self.client: OdooClient | None = None
        # What server_info detects about the database; the review reuses it.
        self.facts: dict = {}

    def _use_env(self) -> None:
        if self.env_path is None or self.client is not None:
            return
        profile, key = load_env(self.env_path)
        self._load, self._api_key, self.preset = (lambda: {profile.name: profile}), (lambda _: key), profile.name

    def profiles(self) -> dict[str, Profile]:
        self._use_env()
        return self._load()

    def bind(self, name: str) -> OdooClient:
        self._use_env()
        if self.client is not None:
            if name != self.client.profile.name:
                raise OdooError(
                    f"This session is pinned to profile {self.client.profile.name}. "
                    f"To work with {name}, open another session: odoo {name}"
                )
            return self.client
        if self.preset and name != self.preset:
            raise OdooError(
                f"This session was opened for profile {self.preset}. "
                f"To work with {name}, open another session: odoo {name}"
            )
        profiles = self._load()
        if name not in profiles:
            raise OdooError(f"Profile {name} does not exist. Profiles: {', '.join(sorted(profiles)) or 'none'}.")
        profile = profiles[name]
        self.client = OdooClient(profile, self._api_key(profile), self._transport)
        return self.client

    def current(self) -> OdooClient:
        if self.client is not None:
            return self.client
        self._use_env()
        if self.preset:
            return self.bind(self.preset)
        raise OdooError("No database is pinned to this session yet: call server_info with the profile name.")


def _odoo_error(response: httpx2.Response, profile: Profile) -> OdooError:
    if response.status_code == 401:
        return OdooError(
            f"Invalid or expired API key for {profile.name}. Create another one in Odoo "
            f"({NEW_API_KEY}) and store it again in the keyring or in the project's .env."
        )
    try:
        body = response.json()
        message = body.get("message") or body.get("name")
    except (ValueError, AttributeError):
        message = None
    message = message or response.text[:300] or response.reason_phrase
    prefix = ERROR_PREFIXES.get(response.status_code, f"Odoo error {response.status_code}")
    return OdooError(f"{prefix}: {message}")
