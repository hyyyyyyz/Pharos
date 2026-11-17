# Pharos backend

The FastAPI service for the Pharos research platform. It provides account and
provider administration, paper and translation jobs, AI conversations, daily
paper discovery, literature search, research projects, and Zotero web
integration.

The full product overview, development instructions, architecture, and license
are maintained in the repository root:

- [README](../README.md)
- [Architecture](../docs/ARCHITECTURE.md)
- [Project decisions](../docs/DECISIONS.md)

## Creating the first administrator

`/api/admin` only lets an existing administrator promote someone else, so a new
instance needs an out-of-band step. Register an account through the normal
sign-up, then do one of the following on the server:

```bash
# Option 1: one-off command against the instance's database
python -m pharos.cli promote you@example.org
python -m pharos.cli admins          # list current administrators

# Option 2: declare it in the environment; applied at every startup
PHAROS_ADMIN_EMAILS=you@example.org,second@example.org
```

Both only promote an account that **already exists**. The first registered
account is deliberately not made admin automatically, and listing an address in
`PHAROS_ADMIN_EMAILS` before it has registered does not reserve it: with open
registration, either behaviour would hand the role to whoever signed up first.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

This package is distributed under AGPL-3.0-or-later.
