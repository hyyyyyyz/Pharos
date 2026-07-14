#!/usr/bin/env bash
#
# Pharos — backend app environment (native arm64, Python 3.13).
# -------------------------------------------------------------------
# The FastAPI backend runs in a plain venv: all its deps ship clean arm64
# wheels, so — unlike the translation ENGINE (see setup_engine_env.sh) — it needs
# no conda / Rosetta. The app spawns the engine worker as a subprocess using the
# engine env's Python, so the two environments stay fully separate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_PYTHON="${BASE_PYTHON:-$HOME/miniconda3/bin/python}"
VENV="${VENV:-$ROOT/backend/.venv}"

log() { printf '\n\033[1;35m[pharos-app]\033[0m %s\n' "$*"; }

log "Base Python: $("$BASE_PYTHON" --version 2>&1)  ($BASE_PYTHON)"
log "Creating venv at $VENV ..."
"$BASE_PYTHON" -m venv "$VENV"

log "Upgrading pip ..."
"$VENV/bin/python" -m pip install --upgrade pip

log "Installing backend (editable) + dev deps ..."
"$VENV/bin/python" -m pip install -e "$ROOT/backend[dev]"

log "Verifying imports ..."
"$VENV/bin/python" - <<'PY'
import fastapi, uvicorn, sqlalchemy, sse_starlette, pymupdf, pydantic_settings  # noqa: F401
import pharos
print("app deps OK; pharos", pharos.__version__)
PY

log "DONE. App env ready: $VENV"
log "Run the API with:  $VENV/bin/uvicorn pharos.main:app --reload --app-dir $ROOT/backend"
