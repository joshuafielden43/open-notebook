#!/bin/sh
# Runtime dependencies are immutable image contents. Startup only validates
# deploy secrets before handing off to CMD.

set -eu
VENV_PY="/app/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "[entrypoint] FATAL: venv python missing; cannot verify deploy secrets."
    exit 1
fi

if ! "$VENV_PY" -m open_notebook.deploy_env; then
    echo "[entrypoint] FATAL: deploy secrets check failed (password + encryption key)."
    exit 1
fi

echo "[entrypoint] Starting Open Notebook."
exec "$@"
