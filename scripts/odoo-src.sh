#!/usr/bin/env bash
# Clona en superficial (o actualiza) las fuentes de Odoo de una rama, para que el
# agente compruebe en el código en vez de suponer.
#
#   scripts/odoo-src.sh <rama> [--update]      p. ej. 19.0, saas-19.2, 20.0
#
# Deja odoo, enterprise y documentation en ~/dev/odoo-src/<rama>/. Las ramas saas
# se mueven: usa --update para traer lo último.
set -uo pipefail

branch="${1:?Uso: odoo-src.sh <rama> [--update]}"
mode="${2:-}"
root="${ODOO_SRC_ROOT:-$HOME/dev/odoo-src}/$branch"

repos=(
  "odoo https://github.com/odoo/odoo.git"
  "enterprise git@github.com:odoo/enterprise.git"
  "documentation https://github.com/odoo/documentation.git"
)

# Acepta la clave de host de GitHub la primera vez sin preguntar.
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o StrictHostKeyChecking=accept-new}"

mkdir -p "$root"
failed=0
for entry in "${repos[@]}"; do
  read -r name url <<<"$entry"
  dest="$root/$name"
  if [[ -d "$dest/.git" ]]; then
    if [[ "$mode" == "--update" ]]; then
      echo "Actualizando $name ($branch)…"
      git -C "$dest" fetch --depth 1 origin "$branch" && git -C "$dest" reset --hard --quiet FETCH_HEAD || failed=1
    else
      echo "$name ya está en $dest (usa --update para actualizarlo)."
    fi
  else
    echo "Clonando $name ($branch)…"
    if ! git clone --quiet --depth 1 --single-branch --branch "$branch" "$url" "$dest"; then
      echo "AVISO: no se pudo clonar $name en la rama $branch." >&2
      rm -rf "$dest"
      failed=1
    fi
  fi
done
exit "$failed"
