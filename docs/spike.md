# Spike

## Verified (2026-10-04)

### In the Odoo 19.0 code (community, `~/dev/odoo-src/19.0/odoo`)

- **Version.** `/json/version` (and `/web/version`) returns `{"version_info", "version"}`, with values such as `19.0` or `saas~19.2+e`. Source: `addons/rpc/controllers/__init__.py`.
- **Calls.** `POST /json/2/<model>/<method>` uses `auth='bearer'`. It takes `ids`, `context` and the rest as keyword arguments, and returns recordsets as ids. Source: `addons/rpc/controllers/json2.py`.
- **Errors.** The body carries `name`, `message`, `arguments`, `context` and `debug` (the full traceback). Source: `serialize_exception` in `odoo/http.py`. The client uses `message` and does not pass `debug` to the agent.
- **Read methods:**
  - `formatted_read_group(domain, groupby, aggregates, having, offset, limit, order)`, with `domain` required (`addons/web/models/models.py`).
  - `get_views(views, options)`, with `@api.readonly`.
  - `fields_get(allfields, attributes)`.
- **Technical models.** The fields used by `model_info` and by the agent's recipes exist in 19.0. In `ir.ui.view`, `groups_id` is renamed to `group_ids`.

### In Claude Code 2.1.289

- The agent loads from the symlink in `~/.claude/agents`.
- With `tools: Read, Grep, Glob, Edit, Write, mcp__odoo`, the agent has no Bash.
- The server declared in `mcpServers` connects when the agent starts, and its tools are named `mcp__odoo__<tool>`.
- The environment reaches the MCP process (`ODOO_PROFILE`, `XDG_CONFIG_HOME`, D-Bus), and `secret-tool` works from it.
- 2026-10-09: with `--agent odoo-dev`, the `Skill` tool invokes the skills linked in `~/.claude/skills` without a prompt, but reading their `guidelines/*.md` needs the `Read(...)` allow rules (denied in `-p` mode without them).

### Against a mocked Odoo (`httpx2.MockTransport`)

- The 7 tools.
- "One session, one database".
- The bearer header and `bin_size`.
- Secret masking.
- The automatic review, and the switch to manual mode when the method does not exist or the AI agent has unverified topics.

### Odoo's official skills against 19.0 (2026-10-09)

The skills in `skills/` come from `odoo/odoo` 20.0 (there is no `skills/` folder in 19.0 or saas-19.1 to saas-19.4). Checked in `~/dev/odoo-src/19.0/odoo`:

- **Access rights.** There is no `ir.access` model: 220 `ir.model.access.csv` files (header `id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink`) and no `ir.access.csv`. Record restrictions are `ir.rule`.
- **QWeb.** `_compile_directive_esc` and `_compile_directive_raw` both return `_compile_directive_out` (`odoo/addons/base/models/ir_qweb.py:2487` and following). For `t-esc` it escapes like `t-out`; for `t-raw` it wraps the value in `Markup` first (`ir_qweb.py:2433-2437`), so `t-raw` still renders unescaped HTML. The skills' 20.0 claims (`t-raw` gone, `t-esc` renders nothing) do not hold. Found by the agent itself in the second end-to-end review, after a wrong first reading of `_compile_directive_raw` only.
- **Routes.** `auth='bearer'` exists (`odoo/http.py:777`), `bearer_scope` does not. `type='json2'` exists (`odoo/http.py:2634`).
- **Same as 20.0.** `models.Constraint`/`Index`/`UniqueIndex` (`odoo/orm/table_objects.py`), `_sql_constraints` ignored with a warning (`odoo/orm/model_classes.py:162`), `self.env._`, `@api.private`, `fields.Domain`, `BaseCommon`, `allow_inherited_tests_method`, `expectUnloadPage`.
- **Missing in 19.0.** The `--unsafe-policy` option (`odoo/tools/config.py`).

### Against pruebas11-grupogr (19.0+e, Odoo Online)

- **2026-10-05, `get_direct_response`:** it exists and answers, with «Default Agent» (GPT-4o) and with «Ask AI» (Gemini 2.5 Flash).
- **Standard agents:**
  - `ai.ai_default_agent`: «Default Agent», no topics.
  - `ai.ai_agent_natural_language_search`: «Ask AI».
  - `ai_website.website_page_generator_agent`: no topics.
- **Ask AI topics:**
  - `ai.ai_topic_natural_language_query`, with 8 tools: adjust_search, compute_report_measures, get_fields, get_menu_details and open_menu_graph, kanban, list and pivot.
  - `ai.ai_topic_information_retrieval_query`, with 3: get_fields, search and read_group.
- **Tools:** their xmlids are `ai.ir_actions_server_<name>`. Each one's code is a single line, `ai['result'] = record._ai_tool_<name>(arguments)`. `READONLY_TOPICS` and `READONLY_TOOLS` were filled in from this, and `check_agents` compares each tool's code with that call.
- **Timeout:** Ask AI from the browser chat failed once with `ReadTimeout` (Odoo waits 30 s for Gemini). Over the API, the trivial query of `smoke.py --review` took 22 s and the AI queried `ir.logging` on its own.

## Pending

### Enterprise code (needs SSH access to `odoo/enterprise`)

- **`_ai_tool_*` methods in `enterprise/ai`:** confirm that their bodies only read (search, read_group, fields_get) or return view actions. Today the whitelist relies on their names and on the tool code only calling them.
- Does `get_direct_response` exist in saas-19.x and 20.0? Verified only in 19.0.
- What changes in Odoo 20: asynchronous sessions and IAP credits.

### Against a test database (needs a profile and a key)

- Which rows `get_direct_response` writes: compare `mail.message` and `ai.*` before and after.
- Whether `get_views` strips nodes according to the key user's groups.
- The error JSON of an expired key (401) and of a missing method (404).
- That the AI query fits in about 100 s on Odoo.sh. On Online it fits: 22 s with Ask AI on pruebas11 (trivial query).
