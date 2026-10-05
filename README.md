# odoo-agent

Agente de Claude Code (`odoo-dev`) para trabajar con bases Odoo 19+ (Online, Odoo.sh y on-premise):

- investiga la base real en **solo lectura** a través de la API JSON-2;
- propone la solución adaptada al hosting;
- **coteja cada paso con la IA de esa misma base** (app IA de Odoo) antes de entregarlo.

Los cambios los aplicas tú.

## Cómo está hecho

- `agents/odoo-dev.md`: el agente. Declara su propio servidor MCP, así que las herramientas de Odoo solo existen mientras corre el agente.
- `src/odoo_agent/`: el servidor MCP.
  - `guard.py`: la lista blanca de lectura y el enmascarado de secretos.
  - `odoo.py`: perfiles, keyring y cliente JSON-2.
  - `review.py`: el cotejo.
  - `server.py`: las 7 herramientas.
- `scripts/odoo-src.sh`: descarga las fuentes de Odoo (odoo, enterprise, documentation) por rama, en `~/dev/odoo-src/<rama>/`.
- `scripts/smoke.py`: prueba de humo contra una base real.

### Por qué es de solo lectura

Para leer la estructura de una base, la clave API tiene que ser de un administrador, y Odoo no tiene claves de solo lectura. El "solo lectura" lo garantiza este código:

- Toda lectura pasa por `guard.prepare_read`. Solo deja pasar `search_read`, `search_count`, `fields_get`, `formatted_read_group` y `get_views`.
- Bloquea los modelos de credenciales.
- Rechaza los campos con pinta de secreto, también en dominios, orden y agrupaciones.
- De `ir.config_parameter` solo lee una lista de claves no sensibles.
- Todo lo que vuelve al agente pasa por `guard.render`, que enmascara secretos y limita el tamaño.
- El agente no tiene Bash ni web. Así no hay otro camino hacia la base que el servidor.

### El cotejo

`ask_odoo_ai` llama a `ai.agent.get_direct_response`, un método público de la app IA de Odoo 19, sobre un agente IA de la propia base. No crea chats.

Es la única llamada que no es pura lectura. Por eso solo se consulta a un agente cuyos temas y herramientas estén en la lista blanca de solo lectura, en `review.py`, que sale del código de enterprise. Mientras esa lista esté vacía, solo se consulta a agentes sin temas.

Si no hay agente válido, si el método no existe en esa versión o si la llamada tarda más de 100 s, devuelve el texto para pegarlo a mano en el chat de la IA. La propuesta queda **PENDIENTE DE COTEJO**.

## Instalación

1. Entorno: `python3 -m venv .venv && .venv/bin/pip install -e . --group dev`.
   - Con uv: `uv sync`, que usa el mismo `.venv`.
2. Agente: `ln -s ~/dev/odoo-agent/agents/odoo-dev.md ~/.claude/agents/odoo-dev.md`.
3. Función de fish `~/.config/fish/functions/odoo.fish`, para abrir una sesión por base:
   ```fish
   function odoo --description 'Agente odoo-dev sobre una base'
       ODOO_PROFILE=$argv[1] claude --agent odoo-dev --add-dir ~/dev/odoo-src $argv[2..]
   end
   ```
4. Permisos en `~/.claude/settings.json`:
   - `allow`: `mcp__odoo__server_info`, `mcp__odoo__search_read`, `mcp__odoo__group_by`, `mcp__odoo__fields`, `mcp__odoo__model_info`, `mcp__odoo__get_view` y `mcp__odoo__ask_odoo_ai`.
   - `deny`: `Edit(~/dev/odoo-src/**)`, que también cubre Write.
5. Fuentes de cada versión que uses: `scripts/odoo-src.sh 19.0`, `scripts/odoo-src.sh saas-19.2`…
   - Las ramas saas se mueven: añade `--update` para actualizarlas.
   - Enterprise necesita tu acceso SSH a GitHub.

## Perfiles y claves

1. Copia `profiles.example.toml` a `~/.config/odoo-agent/profiles.toml` y añade una sección por base.
2. Guarda la clave API de cada perfil en GNOME Keyring desde una terminal. El comando te la pide por teclado y no queda en el historial:
   ```
   secret-tool store --label "odoo-agent: <perfil>" service odoo-agent profile <perfil>
   ```
3. Cuando la clave caduque, el agente te lo dirá. Crea otra en Odoo (Preferencias → Seguridad de la cuenta → Nueva clave API) y repite el paso 2.

### Proyecto con .env

En vez de un perfil y el keyring, una carpeta de proyecto puede llevar un `.env`:

- obligatorias: `ODOO_URL`, `ODOO_HOSTING`, `ODOO_ENV` y `ODOO_API_KEY`;
- opcionales: `ODOO_PROFILE` (por defecto, el nombre de la carpeta), `ODOO_DB` y `ODOO_AI_AGENT`.

Desde esa carpeta, `odoo` sin perfil abre la sesión sobre esa base. Protege el `.env`:

- `chmod 600`;
- ponlo en `.gitignore`;
- deniega `Read(./.env)` en `.claude/settings.json`, para que la clave no acabe en las transcripciones.

## Uso

```
cd <repo de tus módulos, si los hay>
odoo <perfil>
```

Una sesión trabaja con una sola base. Para otra base, abre otra sesión.

Prueba de humo: `.venv/bin/python scripts/smoke.py <perfil> [--cotejo]`.

Tests: `.venv/bin/python -m pytest`.

## Privacidad

Las transcripciones de Claude Code (`~/.claude/projects`) guardan lo que leen las herramientas. Si quieres que duren menos, ajusta `cleanupPeriodDays` en `~/.claude/settings.json`.

La IA de Odoo envía lo que lee y lo que le preguntas a su proveedor (OpenAI o Google), igual que cuando la usas desde el navegador.
