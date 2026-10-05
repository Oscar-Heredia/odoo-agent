#!/usr/bin/env bash
# Shallow-clones (or updates) the Odoo sources of a branch, so the agent checks
# the code instead of guessing.
#
#   scripts/odoo-src.sh <branch> [--update]      e.g. 19.0, saas-19.2, 20.0
#
# Puts odoo, enterprise and documentation in ~/dev/odoo-src/<branch>/. saas
# branches move: use --update to fetch the latest.
set -uo pipefail

branch="${1:?Usage: odoo-src.sh <branch> [--update]}"
mode="${2:-}"
root="${ODOO_SRC_ROOT:-$HOME/dev/odoo-src}/$branch"

repos=(
  "odoo https://github.com/odoo/odoo.git"
  "enterprise git@github.com:odoo/enterprise.git"
  "documentation https://github.com/odoo/documentation.git"
)

# Accept GitHub's host key the first time without asking.
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o StrictHostKeyChecking=accept-new}"

mkdir -p "$root"
failed=0
for entry in "${repos[@]}"; do
  read -r name url <<<"$entry"
  dest="$root/$name"
  if [[ -d "$dest/.git" ]]; then
    if [[ "$mode" == "--update" ]]; then
      echo "Updating $name ($branch)…"
      git -C "$dest" fetch --depth 1 origin "$branch" && git -C "$dest" reset --hard --quiet FETCH_HEAD || failed=1
    else
      echo "$name is already in $dest (use --update to update it)."
    fi
  else
    echo "Cloning $name ($branch)…"
    if ! git clone --quiet --depth 1 --single-branch --branch "$branch" "$url" "$dest"; then
      echo "WARNING: could not clone $name on branch $branch." >&2
      rm -rf "$dest"
      failed=1
    fi
  fi
done
exit "$failed"
