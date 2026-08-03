# Working on Pharos

Read this before changing anything. It is the operating manual for both human
and agent contributors, and it is the file Codex and Claude Code both load.

Two companion documents carry the parts that keep separate contributors pointed
the same way, and it is worth reading them before starting a piece of work
rather than after:

- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what is done, what is next, and
  **what this project deliberately does not do**. The non-goals matter more than
  the goals: they are what stops two contributors building in opposite
  directions.
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — the decisions that are load-bearing
  and the reasons behind them. If a change would reverse one, that is a
  conversation, not a commit.

## What Pharos is

An integrated research platform covering the whole arc: discover → read →
translate → organise → write. Reading and translation is the flagship built
module; writing is roadmap.

It is **not** a translator with a library bolted on. That framing has caused
work to go in the wrong direction before.

## Repository map

```text
backend/        FastAPI, SQLAlchemy, SQLite. The authority for accounts, papers,
                jobs, AI chat, the daily digest, discovery and projects.
  pharos/       API routers, domain services, storage, engine adapter
  engine_worker/ isolated BabelDOC worker, emits NDJSON progress
frontend/       React web companion. The browser/remote writing surface and admin console.
client/         Primary desktop client, built from Zotero source. The local research surface.
site/           Three.js/Vite marketing site (GitHub Pages)
zotero-connector/ Zotero 7/8 extension transport preview
docs/           Architecture, research workflow, roadmap, decisions
```

Which surface a feature belongs to is answered in `docs/DECISIONS.md`. The short
version: anything that happens **while reading a paper** belongs in `client/`;
anything that is **writing, administration, or configuration at scale** belongs
in `frontend/`; anything that needs a model, a key, or a database belongs in
`backend/`.

## Build and test

Each component is independent. Run the tests for what you touched.

```bash
# Backend
cd backend && .venv/bin/pytest

# Web client
cd frontend && npm run build && npx tsc -b
npm test                         # Vitest, sharing vite.config.ts

# Desktop client
cd client
npm run build                    # transpile JS/JSX, compile SCSS
app/scripts/dir_build -p m       # package into app/staging/Pharos.app
app/scripts/run_pharos_dev       # launch with an isolated data directory
test/runtests.sh pharosAPI pharosTranslate pharosTranslateBox pharosChat \
    pharosDaily pharosDiscovery pharosProjects pharosProjectsApi pharosRail \
    pharosRailFooter pharosAdmin pharosProtocol pharosAuth pharosTheme \
    pharosStrings preferences_pharos preferences_pharos_daily
node test/check-locale-parity.js  # both locale files define the same ids
```

When checking the upstream baseline, run it **without `-f`**. That flag stops at
the first failure, so a suite with four known pre-existing failures reports a
misleading `5/6 tests passed -- aborting` and looks like a fresh regression.

For the desktop client, read [`client/BRANDING.md`](client/BRANDING.md) first.
It records a set of Zotero behaviours that **fail silently** — a menu addressed
by index, localization ids resolved per-document, a double hyphen inside an XML
comment that stops a whole window loading. Each cost hours to find and minutes
to fix.

## Invariants

These are not style preferences. Breaking one causes data loss, a security
problem, or a licence violation.

1. **Never test against the real Zotero library.** The production architecture
   intentionally shares Zotero's library after schema compatibility is proven;
   development, tests and CI do not. `-profile` isolates only the Gecko profile,
   **not** Zotero's data directory. Always launch development builds through
   `client/app/scripts/run_pharos_dev`, which passes `-datadir` and refuses
   `~/Zotero`. Do not enable the production shared path while the client core is
   newer than the supported Zotero schema. See
   [`docs/CLIENT_DATA_ARCHITECTURE.md`](docs/CLIENT_DATA_ARCHITECTURE.md).

2. **Never commit secrets.** `.env` is ignored and must stay that way. API keys
   belong in the backend, encrypted with `PHAROS_CREDENTIAL_SECRET`, or in the
   operating system credential store — never in prefs, logs, browser storage or
   Git.

3. **The production server also hosts another site.** Do not restart, reconfigure
   or take down services beyond Pharos's own.

4. **Commits are authored `hyyyyyyz <1783866380@qq.com>`.** Never set a
   repository-local `user.email`; an override once cost eight commits their
   contribution credit. End every commit message with:

   ```text
   Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
   ```

   (or the equivalent trailer for whichever agent wrote it).

5. **AGPL-3.0.** `client/` is derived from Zotero. `COPYING` and the per-file
   copyright notices stay as they are, and derived work stays AGPL. Zotero is a
   trademark of the Corporation for Digital Scholarship; this project does not
   use it and is not affiliated.

6. **Do not extend `zotero.sqlite`.** Shared Zotero entities stay entirely in
   Zotero's schema. Pharos-native local records go in the versioned Pharos
   sidecar and use `(libraryID, key)` identities. Zotero, Vibero and Pharos may
   take turns opening a library, but never use it simultaneously.

## Conventions

- **Match the surrounding code.** `client/` follows Zotero's style (tabs, `let`
  over `const`, no cuddled braces) and has its own `client/CLAUDE.md`. `backend/`
  and `frontend/` follow their own. Do not import one repo's habits into another.
- **Comment the non-obvious, not the obvious.** The comments worth writing are
  the ones that explain why something is the way it is, especially where the
  obvious approach fails silently.
- **Tests pin behaviour that would otherwise regress quietly.** The desktop
  client's context-menu tests exist because the menu is addressed by index and a
  mismatch shows the wrong entry without throwing.
- **Check upstream tests before and after.** `client/` carries Zotero's full
  suite. `itemPane` and `zoteroPane` have four pre-existing failures
  (bibliography-entry mode); anything beyond that is yours.
- **Commit messages say why.** The diff already says what.
