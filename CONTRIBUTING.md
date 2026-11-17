# Contributing to Pharos

Thanks for your interest! Pharos is an open-source (AGPL-3.0) integrated
research platform (discover → read → translate → organise → write), built as a
Zotero-derived desktop client plus a FastAPI backend and a React web companion.
This guide covers the conventions we follow so the codebase stays clean and
reviewable. Read [`AGENTS.md`](AGENTS.md) first: it is the operating manual and
its Invariants take priority over anything here.

## Project layout

```
backend/          FastAPI + SQLAlchemy + SQLite (Python >=3.11; the production image
                  uses 3.12, local setup scripts use 3.13) + engine_worker, which
                  runs in its own isolated environment
frontend/         React + Vite + TypeScript web companion
client/           desktop client built from Zotero source (see client/BRANDING.md)
site/             Vite + Three.js marketing site
zotero-connector/ Zotero 7/8 extension (transport preview)
deploy/           production image and release scripts
scripts/          environment setup scripts
docs/             design docs — start with docs/ARCHITECTURE.md
```

## Development setup

```bash
# Translation engine (isolated osx-64 / Rosetta conda env on Apple Silicon)
bash scripts/setup_engine_env.sh
# Backend app env
bash scripts/setup_app_env.sh
# Frontend
cd frontend && npm ci
# Per-component build and test commands: AGENTS.md, "Build and test"
```

Never commit secrets. API keys live in a local `.env` (git-ignored); commit an
`.env.example` with blank values instead.

**Set `PHAROS_AUTH_SECRET` in that `.env`, not in your shell.** The backend
requires one as soon as it is reachable from outside localhost — which it is
the moment you bind `0.0.0.0` to reach it from a phone or tablet — and a value
exported into a single shell lives only in that process. Restart the server and
every token it ever signed becomes invalid: everyone is silently logged out,
with nothing in the log to say why. Putting it in `.env` is what makes a
restart uneventful.

Still open: the failure is silent by construction (a fresh secret is
indistinguishable from a forged token), so the backend should probably persist
a generated development secret on first boot rather than leaving it ephemeral.
Nobody has done that yet.

## Commit messages — Conventional Commits

Format: `type(scope): summary` (imperative, ≤ 72 chars).

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`.

Examples:

```
feat(engine): stream NDJSON progress from the BabelDOC worker
fix(api): guard against a null mono_pdf_path on finish
docs(architecture): explain the hyperscan / Rosetta workaround
```

Reference issues in the body (`Closes #12`). Keep each commit focused; don't mix
unrelated changes.

## Branches & PRs

- Branch off `main`: `feat/…`, `fix/…`, `docs/…`.
- Open a PR with a clear description of *what* and *why*; link the milestone.
- CI (`.github/workflows/ci.yml`) must pass before merge: backend `ruff` blocking
  rules + `pytest`, frontend `tsc` + `vitest` + build, the Zotero connector tests,
  and the desktop locale-parity check. It does **not** run the desktop client's
  full suite (needs a Gecko runtime, ~30 minutes); run the Pharos suites listed in
  `AGENTS.md` yourself when you touch `client/`. Keep PRs small and reviewable.

## Code style

**Python** — [ruff](https://docs.astral.sh/ruff/) (lint and, via pre-commit,
format), line length 100, type hints on public functions. Config lives in
`backend/pyproject.toml`.

**TypeScript / React** — `tsc -b` must be clean; 2-space indent, function
components + hooks. There is no ESLint or Prettier configuration in the repo.

Install the hooks once so this runs automatically:

```bash
pip install pre-commit && pre-commit install
```

## Tests

Backend uses `pytest` (run it as `python -m pytest` from `backend/`); the web
client uses Vitest (`npm test` in `frontend/`). Add or update tests for
behavioural changes; the NDJSON worker contract and the job pipeline in
particular should stay covered.

## License of contributions

By contributing you agree that your contributions are licensed under the
project's **AGPL-3.0** license.
