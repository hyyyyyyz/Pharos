# Contributing to Pharos

Thanks for your interest! Pharos is an open-source (AGPL-3.0) research-paper
translator and reading assistant. This guide covers the conventions we follow so
the codebase stays clean and reviewable.

## Project layout

```
backend/     FastAPI app (native arm64, Python 3.13) + engine_worker (osx-64 env)
frontend/    React + Vite + TypeScript
scripts/     environment setup scripts
docs/        design docs — start with docs/ARCHITECTURE.md
```

## Development setup

```bash
# Translation engine (isolated osx-64 / Rosetta conda env on Apple Silicon)
bash scripts/setup_engine_env.sh
# Backend app env + frontend: see docs/ARCHITECTURE.md (added in M2/M3)
```

Never commit secrets. API keys live in a local `.env` (git-ignored); commit an
`.env.example` with blank values instead.

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
- CI (lint + tests) must pass before merge. Keep PRs small and reviewable.

## Code style

**Python** — [ruff](https://docs.astral.sh/ruff/) (lint) + [black](https://black.readthedocs.io/)
(format), line length 100, type hints on public functions. Config lives in
`backend/pyproject.toml`.

**TypeScript / React** — ESLint + Prettier, 2-space indent, function components +
hooks.

Install the hooks once so this runs automatically:

```bash
pip install pre-commit && pre-commit install
```

## Tests

Backend uses `pytest`. Add or update tests for behavioural changes; the NDJSON
worker contract and the job pipeline in particular should stay covered.

## License of contributions

By contributing you agree that your contributions are licensed under the
project's **AGPL-3.0** license.
