"""Conexión con una base Odoo por JSON-2: perfiles, clave del keyring y cliente.

Una sesión trabaja con una sola base: el perfil de ODOO_PROFILE, o el primero
que se use, queda fijado. Así los datos de un cliente nunca acaban en la base ni
en la IA de otro.
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
    403: "Odoo denegó el acceso",
    404: "No existe en esta base",
    409: "Conflicto de bloqueo en Odoo; reintenta",
    422: "Odoo rechazó la llamada",
}


class OdooError(Exception):
    """Fallo de Odoo o de la conexión, con un mensaje pensado para el agente."""


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
        raise OdooError(f"No existe {path}. Créalo a partir de profiles.example.toml del repo odoo-agent.")
    with path.open("rb") as f:
        data = tomllib.load(f)
    profiles = {}
    for name, raw in data.get("profiles", {}).items():
        missing = [key for key in ("url", "hosting", "env") if key not in raw]
        if missing:
            raise OdooError(f"Al perfil {name} le falta {', '.join(missing)} en {path}.")
        if raw["hosting"] not in HOSTINGS:
            raise OdooError(f"Perfil {name}: hosting tiene que ser {', '.join(HOSTINGS)}.")
        if raw["env"] not in ENVIRONMENTS:
            raise OdooError(f"Perfil {name}: env tiene que ser {', '.join(ENVIRONMENTS)}.")
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
    """Clave API del perfil, leída de GNOME Keyring. Nunca se guarda en disco."""
    store = f'secret-tool store --label "odoo-agent: {profile.name}" service {KEYRING_SERVICE} profile {profile.name}'
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", KEYRING_SERVICE, "profile", profile.name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OdooError(f"No se pudo leer el keyring: {e}") from None
    key = result.stdout.strip()
    if result.returncode != 0 or not key:
        # secret-tool no dice nada si la clave no existe; si escribe algo, es que falló el keyring.
        detail = f" (secret-tool: {result.stderr.strip()})" if result.stderr.strip() else ""
        raise OdooError(
            f"No hay clave API del perfil {profile.name} en el keyring{detail}. "
            f"Guárdala desde una terminal con: {store}"
        )
    return key


def source_branch(version: str) -> str:
    """Rama de las fuentes para una versión de servidor: 'saas~19.2+e' → 'saas-19.2'."""
    match = re.match(r"(saas~)?(\d+)\.(\d+)", version)
    if not match:
        raise OdooError(f"Versión de Odoo no reconocida: {version}")
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
            raise OdooError(f"No se pudo conectar con {self.profile.url}: {e}") from None
        if response.status_code != 200:
            raise _odoo_error(response, self.profile)
        return response.json()

    async def read(self, model: str, method: str, params: dict) -> Any:
        """La única vía de lectura: todo pasa por la lista blanca de guard."""
        return await self._call(model, method, guard.prepare_read(model, method, params))

    async def direct_response(self, agent_id: int, prompt: str, context_message: str, timeout: float) -> Any:
        """La única llamada que no es pura lectura: pide su opinión a un agente de la app IA."""
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
            raise OdooError(f"Odoo no respondió en {timeout:.0f} s a {model}.{method}.") from None
        except httpx2.HTTPError as e:
            raise OdooError(f"No se pudo conectar con {self.profile.url}: {e}") from None
        if response.status_code != 200:
            raise _odoo_error(response, self.profile)
        return response.json()


class Session:
    """Una sesión, una base."""

    def __init__(self, load=load_profiles, api_key=keyring_api_key, transport=None, preset: str | None = None):
        self._load = load
        self._api_key = api_key
        self._transport = transport
        self.preset = preset if preset is not None else os.environ.get("ODOO_PROFILE") or None
        self.client: OdooClient | None = None
        # Lo que server_info detecta de la base; el cotejo lo reutiliza.
        self.facts: dict = {}

    def profiles(self) -> dict[str, Profile]:
        return self._load()

    def bind(self, name: str) -> OdooClient:
        if self.client is not None:
            if name != self.client.profile.name:
                raise OdooError(
                    f"Esta sesión está fijada al perfil {self.client.profile.name}. "
                    f"Para trabajar con {name}, abre otra sesión: odoo {name}"
                )
            return self.client
        if self.preset and name != self.preset:
            raise OdooError(
                f"Esta sesión se abrió para el perfil {self.preset}. "
                f"Para trabajar con {name}, abre otra sesión: odoo {name}"
            )
        profiles = self._load()
        if name not in profiles:
            raise OdooError(f"No existe el perfil {name}. Perfiles: {', '.join(sorted(profiles)) or 'ninguno'}.")
        profile = profiles[name]
        self.client = OdooClient(profile, self._api_key(profile), self._transport)
        return self.client

    def current(self) -> OdooClient:
        if self.client is not None:
            return self.client
        if self.preset:
            return self.bind(self.preset)
        raise OdooError("Todavía no hay base fijada en esta sesión: llama a server_info con el nombre del perfil.")


def _odoo_error(response: httpx2.Response, profile: Profile) -> OdooError:
    if response.status_code == 401:
        return OdooError(
            f"Clave API inválida o caducada para {profile.name}. Crea otra en Odoo "
            "(Preferencias → Seguridad de la cuenta → Nueva clave API) y vuelve a guardarla en el keyring."
        )
    try:
        body = response.json()
        message = body.get("message") or body.get("name")
    except (ValueError, AttributeError):
        message = None
    message = message or response.text[:300] or response.reason_phrase
    prefix = ERROR_PREFIXES.get(response.status_code, f"Error {response.status_code} de Odoo")
    return OdooError(f"{prefix}: {message}")
