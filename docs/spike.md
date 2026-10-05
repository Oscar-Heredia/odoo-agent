# Spike

## Comprobado (2026-10-04)

### En el código de Odoo 19.0 (community, `~/dev/odoo-src/19.0/odoo`)

- **Versión.** `/json/version` (y `/web/version`) devuelve `{"version_info", "version"}`, con valores como `19.0` o `saas~19.2+e`. Fuente: `addons/rpc/controllers/__init__.py`.
- **Llamadas.** `POST /json/2/<modelo>/<método>` usa `auth='bearer'`. Recibe `ids`, `context` y el resto como argumentos con nombre, y devuelve los recordsets como ids. Fuente: `addons/rpc/controllers/json2.py`.
- **Errores.** El cuerpo trae `name`, `message`, `arguments`, `context` y `debug` (la traza completa). Fuente: `serialize_exception` en `odoo/http.py`. El cliente usa `message` y no pasa `debug` al agente.
- **Métodos de lectura:**
  - `formatted_read_group(domain, groupby, aggregates, having, offset, limit, order)`, con `domain` obligatorio (`addons/web/models/models.py`).
  - `get_views(views, options)`, con `@api.readonly`.
  - `fields_get(allfields, attributes)`.
- **Modelos técnicos.** Los campos que usan `model_info` y las recetas del agente existen en 19.0. En `ir.ui.view`, `groups_id` pasa a llamarse `group_ids`.

### En Claude Code 2.1.289

- El agente se carga desde el symlink de `~/.claude/agents`.
- Con `tools: Read, Grep, Glob, Edit, Write, mcp__odoo`, el agente no tiene Bash.
- El servidor declarado en `mcpServers` se conecta al arrancar el agente, y sus herramientas se llaman `mcp__odoo__<herramienta>`.
- El entorno llega al proceso MCP (`ODOO_PROFILE`, `XDG_CONFIG_HOME`, D-Bus), y `secret-tool` funciona desde él.

### Contra un Odoo simulado (`httpx2.MockTransport`)

- Las 7 herramientas.
- "Una sesión, una base".
- La cabecera bearer y `bin_size`.
- El enmascarado de secretos.
- El cotejo automático, y el paso a modo manual cuando el método no existe o el agente IA tiene temas sin verificar.

## Pendiente

### Código de enterprise (necesita acceso SSH a `odoo/enterprise`)

- ¿Existe `ai.agent.get_direct_response` en las ramas de tus bases (19.0, saas-19.x, 20.0)? Mirarlo en `enterprise/ai/models/ai_agent.py`.
- Lista blanca de temas y herramientas de serie que solo leen, por xmlid. Va en `READONLY_TOPICS` y `READONLY_TOOLS` de `review.py`. Mientras esté vacía, solo se consulta a agentes IA sin temas.
- El xmlid del agente por defecto (`DEFAULT_AGENT_XMLID`).
- En Odoo 20, qué cambia: sesiones asíncronas y créditos IAP.

### Contra una base de prueba (necesita perfil y clave)

- Ejecutar `.venv/bin/python scripts/smoke.py <perfil> --cotejo`: lecturas reales, rechazo del guard y una consulta trivial a la IA, con su respuesta y su latencia.
- Qué filas escribe `get_direct_response`: comparar `mail.message` y `ai.*` antes y después.
- Si `get_views` quita nodos según los grupos del usuario de la clave.
- El JSON de error de una clave caducada (401) y de un método inexistente (404).
- Que la consulta a la IA quepa en unos 100 s en Online y en Odoo.sh.
