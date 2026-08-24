#!/usr/bin/env sh
# Host preflight: door (password) + vault (encryption key).
# Single policy lives in open_notebook.deploy_env — this is only a launcher.
#
# Usage:
#   scripts/check-deploy-env.sh
#   scripts/check-deploy-env.sh /path/to/.env
#   make check-env

set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-}"

if [ -z "$ENV_FILE" ] && [ -f "$ROOT/.env" ]; then
  ENV_FILE="$ROOT/.env"
fi

cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  if [ -n "$ENV_FILE" ]; then
    exec uv run python -m open_notebook.deploy_env "$ENV_FILE"
  fi
  exec uv run python -m open_notebook.deploy_env
fi

if [ -x "$ROOT/.venv/bin/python" ]; then
  if [ -n "$ENV_FILE" ]; then
    exec "$ROOT/.venv/bin/python" -m open_notebook.deploy_env "$ENV_FILE"
  fi
  exec "$ROOT/.venv/bin/python" -m open_notebook.deploy_env
fi

echo "check-deploy-env: need uv or .venv/bin/python" >&2
exit 2
