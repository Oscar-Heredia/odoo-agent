# Upstream

The four skills in this folder are Odoo's official skills, copied unchanged:

- Repository: https://github.com/odoo/odoo, folder `skills/`
- Branch: `20.0`
- Commit: `46e688e2cf4f03cbe459ea483dd46c46705076fa` (2026-10-09)
- License: LGPL-3, the license of the Odoo repository

`odoo-git` is left out: it covers contributing to Odoo itself (commit tags,
target branches, PRs against `odoo/odoo`), and no other skill references it.

They are written for Odoo 20.0. The 19.x exceptions live in
`agents/odoo-dev.md`, not here, so these files stay identical to upstream.

## Update

```sh
tmp=$(mktemp -d)
git clone --quiet --depth 1 --filter=blob:none --sparse -b 20.0 https://github.com/odoo/odoo.git "$tmp"
git -C "$tmp" sparse-checkout set skills
for s in odoo-guidelines odoo-security odoo-review odoo-web-guidelines; do
  rm -rf "skills/$s" && cp -r "$tmp/skills/$s" skills/
done
git -C "$tmp" log -1 --format='%H %cs'   # write it above
rm -rf "$tmp"
.venv/bin/python -m pytest tests/test_skills.py
```

Then review the diff: a new rule may contradict the 19.x exceptions in the agent.
