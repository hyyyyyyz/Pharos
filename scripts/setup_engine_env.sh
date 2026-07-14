#!/usr/bin/env bash
#
# Pharos — translation engine environment setup (macOS / Apple Silicon)
# -----------------------------------------------------------------------
# Creates an isolated conda environment that holds the AGPL-3.0 BabelDOC
# engine (installed via its sanctioned wrapper `pdf2zh-next`).
#
# WHY osx-64 / Rosetta:
#   BabelDOC hard-depends on `hyperscan`, which publishes NO macOS arm64
#   wheels (and no cp313 wheels at all). A naive `pip install` on Apple
#   Silicon therefore tries to build hyperscan from source and fails.
#   Creating the env under CONDA_SUBDIR=osx-64 installs an x86_64 Python
#   (run transparently via Rosetta 2), so the prebuilt x86_64 wheels for
#   hyperscan / onnxruntime / pymupdf install cleanly with zero compiling.
#   Translation is network/LLM-bound, so the Rosetta overhead is negligible.
#
# The engine is kept in its OWN environment and invoked as a subprocess by
# the FastAPI backend — never imported in-process. This quarantines the
# native deps and preserves the AGPL arm's-length boundary.
#
# Usage:  bash scripts/setup_engine_env.sh
# Env overrides: CONDA=..., ENV_NAME=..., PY_VER=..., PDF2ZH_VERSION=...
set -euo pipefail

CONDA="${CONDA:-$HOME/miniconda3/bin/conda}"
ENV_NAME="${ENV_NAME:-pharos-engine}"
PY_VER="${PY_VER:-3.12}"
PDF2ZH_VERSION="${PDF2ZH_VERSION:-2.9.0}"
# Resilient pip networking (flaky connections to PyPI are common). Override
# PIP_INDEX_URL to use a faster mirror, e.g.
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/setup_engine_env.sh
PIP_NET_OPTS="--retries 8 --timeout 180"

log() { printf '\n\033[1;36m[pharos]\033[0m %s\n' "$*"; }

log "conda binary: $CONDA"
"$CONDA" --version

if [ "$(uname -m)" = "arm64" ] && ! /usr/bin/pgrep -q oahd; then
  log "WARNING: Rosetta 2 does not appear to be running. Install it with:"
  log "  softwareupdate --install-rosetta --agree-to-license"
fi

if "$CONDA" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  log "Env '$ENV_NAME' already exists — skipping creation (re-run is idempotent)."
else
  log "Creating osx-64 (Rosetta) env '$ENV_NAME' with Python $PY_VER ..."
  CONDA_SUBDIR=osx-64 "$CONDA" create -y -n "$ENV_NAME" "python=$PY_VER"
fi

log "Interpreter architecture (expect x86_64):"
"$CONDA" run -n "$ENV_NAME" python -c "import platform; print(platform.machine())"

log "Upgrading pip / setuptools / wheel ..."
"$CONDA" run -n "$ENV_NAME" python -m pip install $PIP_NET_OPTS --upgrade pip setuptools wheel

# `cryptography` ships no wheel matching this osx-64 conda Python, so pip would
# try to compile it from source — which fails under Rosetta (no x86_64 CLT
# toolchain: libxcrun.dylib is arm64-only). Install the prebuilt osx-64 build
# from conda first; pip then sees the requirement already satisfied.
log "Pre-installing conda-prebuilt native deps (cryptography) to avoid a Rosetta source build ..."
CONDA_SUBDIR=osx-64 "$CONDA" install -y -n "$ENV_NAME" cryptography

log "Installing pdf2zh-next==$PDF2ZH_VERSION (pulls BabelDOC + native deps) ..."
"$CONDA" run -n "$ENV_NAME" python -m pip install $PIP_NET_OPTS "pdf2zh-next==$PDF2ZH_VERSION"

log "Verifying native imports ..."
"$CONDA" run -n "$ENV_NAME" python - <<'PY'
import platform
print("machine        :", platform.machine())
import hyperscan
print("hyperscan      : OK")
import onnxruntime
print("onnxruntime    :", onnxruntime.__version__)
import pymupdf
print("pymupdf        :", pymupdf.__doc__.strip().splitlines()[0] if pymupdf.__doc__ else "OK")
import babeldoc
print("babeldoc       :", getattr(babeldoc, "__version__", "?"))
import pdf2zh_next
print("pdf2zh_next    :", getattr(pdf2zh_next, "__version__", "?"))
from pdf2zh_next.high_level import do_translate_async_stream  # noqa: F401
print("high_level API : do_translate_async_stream import OK")
PY

log "DONE. Engine env '$ENV_NAME' is ready."
log "Next: cache the layout model with a warmup, then run a test translation."
