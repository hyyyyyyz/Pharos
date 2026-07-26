# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY README.md LICENSE ./
COPY backend ./backend

# Keep the FastAPI runtime and BabelDOC worker in separate Python environments.
# The API invokes the engine interpreter as a subprocess, preserving the same
# dependency and crash-isolation boundary used in local development.
RUN python -m venv /opt/pharos-app \
    && /opt/pharos-app/bin/pip install --upgrade pip setuptools wheel \
    && /opt/pharos-app/bin/pip install ./backend

RUN python -m venv /opt/pharos-engine \
    && /opt/pharos-engine/bin/pip install --upgrade pip setuptools wheel \
    && /opt/pharos-engine/bin/pip install "pdf2zh-next==2.9.0"


FROM python:3.12-slim-bookworm AS runtime

ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Pharos backend" \
      org.opencontainers.image.description="FastAPI research platform with an isolated BabelDOC translation worker" \
      org.opencontainers.image.source="https://github.com/hyyyyyyz/Pharos" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV PATH=/opt/pharos-app/bin:$PATH \
    PYTHONPATH=/app/backend \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PHAROS_DATA_DIR=/data \
    PHAROS_ENGINE_PYTHON=/opt/pharos-engine/bin/python \
    HOME=/home/pharos \
    XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tini \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 pharos \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/pharos pharos \
    && mkdir -p /app /data /cache \
    && chown -R pharos:pharos /app /data /cache /home/pharos

COPY --from=builder /opt/pharos-app /opt/pharos-app
COPY --from=builder /opt/pharos-engine /opt/pharos-engine
COPY --chown=pharos:pharos backend /app/backend

USER 1000:1000
WORKDIR /app

EXPOSE 8400

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8400/api/health', timeout=3); assert r.status == 200 and json.load(r).get('status') == 'ok'"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "pharos.main:app", "--host", "0.0.0.0", "--port", "8400", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
