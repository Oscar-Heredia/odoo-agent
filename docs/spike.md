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

### Contra pruebas11-grupogr (19.0+e, Odoo Online)

- **2026-10-05, `get_direct_response`:** existe y responde, con «Default Agent» (GPT-4o) y con «Ask AI» (Gemini 2.5 Flash).
- **Agentes de serie:**
  - `ai.ai_default_agent`: «Default Agent», sin temas.
  - `ai.ai_agent_natural_language_search`: «Ask AI».
  - `ai_website.website_page_generator_agent`: sin temas.
- **Temas de Ask AI:**
  - `ai.ai_topic_natural_language_query`, con 8 herramientas: adjust_search, compute_report_measures, get_fields, get_menu_details y open_menu_graph, kanban, list y pivot.
  - `ai.ai_topic_information_retrieval_query`, con 3: get_fields, search y read_group.
- **Herramientas:** sus xmlids son `ai.ir_actions_server_<nombre>`. El código de cada una es una sola línea, `ai['result'] = record._ai_tool_<nombre>(argumentos)`. Con eso se rellenaron `READONLY_TOPICS` y `READONLY_TOOLS`, y `check_agents` compara el código de cada herramienta con esa llamada.
- **Tiempo de espera:** Ask AI desde el chat del navegador falló una vez con `ReadTimeout` (Odoo espera 30 s a Gemini). Por API, la consulta trivial de `smoke.py --cotejo` tardó 22 s y la IA consultó `ir.logging` por su cuenta.

## Pendiente

### Código de enterprise (necesita acceso SSH a `odoo/enterprise`)

- **Métodos `_ai_tool_*` de `enterprise/ai`:** confirmar que su cuerpo solo lee (search, read_group, fields_get) o devuelve acciones de vista. Hoy la lista blanca se apoya en su nombre y en que el código de la herramienta solo los llama.
- ¿Existe `get_direct_response` en saas-19.x y en 20.0? Comprobado solo en 19.0.
- En Odoo 20, qué cambia: sesiones asíncronas y créditos IAP.

### Contra una base de prueba (necesita perfil y clave)

- Qué filas escribe `get_direct_response`: comparar `mail.message` y `ai.*` antes y después.
- Si `get_views` quita nodos según los grupos del usuario de la clave.
- El JSON de error de una clave caducada (401) y de un método inexistente (404).
- Que la consulta a la IA quepa en unos 100 s en Odoo.sh. En Online cabe: 22 s con Ask AI en pruebas11 (consulta trivial).
