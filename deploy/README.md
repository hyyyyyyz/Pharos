# Pharos production deployment

This deployment is deliberately isolated from every existing service on
`claude-tri`:

- the web client and API bind only `127.0.0.1:8400`;
- Docker resources are named `pharos-*`;
- persistent files stay below `/home/winbeau/pharos/`;
- no command touches `new-api`, `cli-proxy-api-*`, Caddy, `/opt`, or another
  user's home directory;
- the deploy script records and rechecks the protected production baseline;
- the server pulls an image built by GitHub Actions and never builds the app.

## Topology

```text
pharos.selab.top
        │ system cloudflared.service
        ▼
127.0.0.1:8400 ── pharos-api
                     ├── /           compiled React workbench
                     ├── /api/*      FastAPI core
                     └── /home/winbeau/pharos/shared
                         data · cache · backups · secrets
```

One immutable image contains the compiled React workbench, FastAPI core, and
isolated BabelDOC worker. The API uses one Uvicorn worker and allows one
translation job at a time. The container is capped at 1.8 GB RAM plus limited
swap so a difficult PDF fails inside Pharos instead of exhausting the host that
serves `api.selab.top`.

## Release flow

1. Push a commit touching `frontend/**`, `backend/**`, or `deploy/**`.
2. `.github/workflows/backend-image.yml` builds and smoke-tests a Linux/amd64
   complete Linux/amd64 image tagged
   `ghcr.io/hyyyyyyz/pharos:sha-<12-char-sha>`.
3. From a clean checkout, run:

   ```bash
   deploy/pharosctl deploy
   ```

4. The remote script backs up SQLite, pulls the immutable image, recreates only
   `pharos-api`, waits for `/api/health`, and automatically restores the
   previous web/API image if the new one is unhealthy.

Useful commands:

```bash
deploy/pharosctl status
deploy/pharosctl rollback
```

Cloudflare Tunnel is a one-time host bootstrap, intentionally outside the app
release lifecycle. The root-owned `cloudflared.service` uses a remotely managed
Tunnel and routes `pharos.selab.top` to `http://127.0.0.1:8400`. Application
deploys and rollbacks never restart or rewrite that system service.

## Server data layout

```text
/home/winbeau/pharos/
├── current -> releases/<active-release>
├── releases/                 deployment metadata and scripts
├── state/                    active/previous image and deployment lock
└── shared/
    ├── data/                 SQLite and PDF blobs
    ├── cache/                writable engine HOME, models, fonts and config
    ├── tmp/                  bounded Pharos work files
    ├── backups/              last eight online SQLite backups
    ├── secrets/backend.env   mode 0600, never committed
    └── cloudflared/          pinned bootstrap binary metadata (not the token)
```

Code rollback never overwrites the live database. A database backup is created
before every deployment and manual rollback, but restoring one is intentionally
an operator decision because doing it automatically could discard new user data.
