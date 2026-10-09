# odoo-agent

A Claude Code agent (`odoo-dev`) for working with Odoo 19+ databases (Online, Odoo.sh and on-premise):

- investigates the real database **read-only** through the JSON-2 API;
- proposes a solution suited to the hosting;
- **cross-checks every step with that same database's AI** (the Odoo AI app) before delivering it.

You apply the changes yourself.

## Repository layout

```
odoo-agent/
├── README.md               installation, usage and how the review works
├── pyproject.toml
├── profiles.example.toml   profile template (alternative to a project .env)
├── agents/
│   └── odoo-dev.md         instructions for the odoo-dev agent
├── docs/
│   └── spike.md            what has been verified and what is pending
├── skills/                 Odoo's official skills, vendored (see skills/UPSTREAM.md)
├── scripts/
│   ├── smoke.py            smoke test against a database (--review)
│   └── odoo-src.sh         downloads the Odoo sources
├── src/odoo_agent/
│   ├── guard.py            read whitelist and secret masking
│   ├── odoo.py             JSON-2 client, profiles and .env
│   ├── review.py           cross-check with the database's AI
│   └── server.py           MCP server with the 7 tools
└── tests/
    ├── test_guard.py
    ├── test_env.py
    ├── test_own_modules.py
    ├── test_review.py
    └── test_skills.py         vendored skills and the agent's references to them
```

## How it is built

- `agents/odoo-dev.md`: the agent. It declares its own MCP server, so the Odoo tools only exist while the agent runs.
- `src/odoo_agent/`: the MCP server.
  - `guard.py`: the read whitelist and secret masking.
  - `odoo.py`: profiles, keyring and JSON-2 client.
  - `review.py`: the cross-check.
  - `server.py`: the 7 tools.
- `skills/`: Odoo's official skills for writing and reviewing addon code (see «Odoo's official skills»).
- `scripts/odoo-src.sh`: downloads the Odoo sources (odoo, enterprise, documentation) per branch into `~/dev/odoo-src/<branch>/`.
- `scripts/smoke.py`: smoke test against a real database.

### Why it is read-only

To read a database's structure the API key has to belong to an administrator, and Odoo has no read-only keys. Read-only access is enforced by this code:

- Every read goes through `guard.prepare_read`. It only lets `search_read`, `search_count`, `fields_get`, `formatted_read_group` and `get_views` through.
- It blocks credential models.
- It rejects secret-looking fields, also in domains, ordering and grouping.
- From `ir.config_parameter` it only reads a list of non-sensitive keys.
- Everything returned to the agent goes through `guard.render`, which masks secrets and caps the size.
- The agent has neither Bash nor web access, so the server is its only path to the database.

### The review

`ask_odoo_ai` calls `ai.agent.get_direct_response`, a public method of the Odoo 19 AI app, on an AI agent of the database itself. It does not create chats.

It is the only call that is not a pure read. That is why an agent is only queried if its topics and tools are on the read-only whitelist in `review.py`:

- **Topics:** the two standard topics of Ask AI, «Natural Language Search» and «Information retrieval».
- **Tools:** their 10 standard tools, which search, group, read fields and open views. Each one only counts if its code in the database is exactly the standard call to its `_ai_tool_*` method. If someone edits it, the agent no longer counts as read-only.

Order of preference:
1. The agent set in the profile (`ai_agent` or `ODOO_AI_AGENT`).
2. An agent with read-only topics, such as Ask AI, because it can query the database.
3. «Default Agent», which has no topics and answers without seeing the data.

If the first one fails, for example because Gemini does not answer within the 30 s Odoo waits, the next one is tried within a total budget of 100 s. The answer states which agent replied and whether it could read the database.

If there is no usable agent, the method does not exist in that version, or time runs out, it returns the text to paste by hand into the AI chat. The proposal stays **REVIEW PENDING**.

## Odoo's official skills

`skills/` holds four of the skills Odoo publishes in `odoo/odoo` (`odoo-guidelines`, `odoo-security`, `odoo-review`, `odoo-web-guidelines`), copied unchanged from branch 20.0; `skills/UPSTREAM.md` gives the commit and how to update them. The agent invokes them with the `Skill` tool when a step carries code, or when you ask it to review yours.

They are written for Odoo 20.0. The agent lets the source of the database's branch win, and its prompt lists the known 19.0 differences: no `ir.access` model (access rights stay in `ir.model.access.csv` and `ir.rule`), `t-esc` still escapes and `t-raw` still renders raw HTML, and no `bearer_scope`. `odoo-git`, Odoo's own contribution rules, is left out.

## Installation

1. Environment: `python3 -m venv .venv && .venv/bin/pip install -e . --group dev`.
   - With uv: `uv sync`, which uses the same `.venv`.
2. Agent: `ln -s ~/dev/odoo-agent/agents/odoo-dev.md ~/.claude/agents/odoo-dev.md`.
3. Skills: `for s in odoo-guidelines odoo-security odoo-review odoo-web-guidelines; ln -s ~/dev/odoo-agent/skills/$s ~/.claude/skills/$s; end`.
4. A fish function in `~/.config/fish/functions/odoo.fish`, to open one session per database:
   ```fish
   function odoo --description 'odoo-dev agent on a database'
       ODOO_PROFILE=$argv[1] claude --agent odoo-dev --add-dir ~/dev/odoo-src $argv[2..]
   end
   ```
5. Permissions in `~/.claude/settings.json`:
   - `allow`: `mcp__odoo__server_info`, `mcp__odoo__search_read`, `mcp__odoo__group_by`, `mcp__odoo__fields`, `mcp__odoo__model_info`, `mcp__odoo__get_view` and `mcp__odoo__ask_odoo_ai`; plus `Read(~/.claude/skills/odoo-*/**)` and `Read(~/dev/odoo-agent/skills/**)`, so the agent can read the skills' `guidelines/` files.
   - `deny`: `Edit(~/dev/odoo-src/**)`, which also covers Write.
6. Sources for every version you use: `scripts/odoo-src.sh 19.0`, `scripts/odoo-src.sh saas-19.2`…
   - saas branches move: add `--update` to refresh them.
   - Enterprise needs your SSH access to GitHub.

## Profiles and keys

1. Copy `profiles.example.toml` to `~/.config/odoo-agent/profiles.toml` and add one section per database.
2. Store each profile's API key in GNOME Keyring from a terminal. The command prompts for it, so it never lands in your shell history:
   ```
   secret-tool store --label "odoo-agent: <profile>" service odoo-agent profile <profile>
   ```
3. When the key expires, the agent will tell you. Create another one in Odoo (Preferences → Account Security → New API Key) and repeat step 2.

### Project with a .env

Instead of a profile and the keyring, a project folder can carry a `.env`:

- required: `ODOO_URL`, `ODOO_HOSTING`, `ODOO_ENV` and `ODOO_API_KEY`;
- optional: `ODOO_PROFILE` (defaults to the folder name), `ODOO_DB` and `ODOO_AI_AGENT`.

From that folder, `odoo` without a profile opens the session on that database. Protect the `.env`:

- `chmod 600`;
- add it to `.gitignore`;
- deny `Read(./.env)` in `.claude/settings.json`, so the key never ends up in transcripts.

## Usage

```
cd <your modules repo, if any>
odoo <profile>
```

A session works with a single database. For another database, open another session.

Smoke test: `.venv/bin/python scripts/smoke.py <profile> [--review]`.

Tests: `.venv/bin/python -m pytest`.

## Privacy

Claude Code transcripts (`~/.claude/projects`) keep what the tools read. If you want them to last less, set `cleanupPeriodDays` in `~/.claude/settings.json`.

Odoo's AI sends what it reads and what you ask it to its provider (OpenAI or Google), just as when you use it from the browser.
