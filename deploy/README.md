# Pharos production deployment

This deployment is deliberately isolated from every existing service on
`claude-tri`:

- the API binds only `127.0.0.1:8400`;
- Docker resources are named `pharos-*`;
- persistent files stay below `/home/winbeau/pharos/`;
- no command touches `new-api`, `cli-proxy-api-*`, Caddy, `/opt`, or another
  user's home directory;
- the deploy script records and rechecks the protected production baseline;
- the server pulls an image built by GitHub Actions and never builds the app.

## Topology

```text
pharos-api.selab.top
        │ Cloudflare Tunnel: pharos-prod
        ▼
pharos-cloudflared ── pharos-net ── pharos-api:8400
                                         │
                          /home/winbeau/pharos/shared
                          data · cache · backups · secrets
```

The API uses one Uvicorn worker and allows one BabelDOC translation job at a
time. The container is capped at 1.8 GB RAM plus limited swap so a difficult PDF
fails inside Pharos instead of exhausting the host that serves `api.selab.top`.

## Release flow

1. Push a commit touching `backend/**` or `deploy/**`.
2. `.github/workflows/backend-image.yml` builds and smoke-tests a Linux/amd64
   image tagged `ghcr.io/hyyyyyyz/pharos:sha-<12-char-sha>`.
3. From a clean checkout, run:

   ```bash
   deploy/pharosctl deploy
   ```

4. The remote script backs up SQLite, pulls the immutable image, recreates only
   `pharos-api`, waits for `/api/health`, and automatically restores the
   previous image if the new one is unhealthy.

Useful commands:

```bash
deploy/pharosctl status
deploy/pharosctl rollback
deploy/pharosctl tunnel-login
deploy/pharosctl tunnel-create
```

`tunnel-login` is the sole interactive step: Cloudflare prints a browser URL
that must be approved by a user with access to the `selab.top` zone. After that,
`tunnel-create` creates a new `pharos-prod` tunnel, creates/updates only the
`pharos-api.selab.top` DNS record, pins the official cloudflared image by digest,
and starts `pharos-cloudflared` with `restart: unless-stopped`.

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
    └── cloudflared/          cert, tunnel credential and config
```

Code rollback never overwrites the live database. A database backup is created
before every deployment and manual rollback, but restoring one is intentionally
an operator decision because doing it automatically could discard new user data.
