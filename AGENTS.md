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

For work on research automation, also read the full Harness set before touching
code: [`HARNESS_LANDSCAPE.md`](docs/HARNESS_LANDSCAPE.md),
[`HARNESS_ARCHITECTURE.md`](docs/HARNESS_ARCHITECTURE.md),
[`HARNESS_WORKFLOWS.md`](docs/HARNESS_WORKFLOWS.md), and
[`HARNESS_IMPLEMENTATION_PLAN.md`](docs/HARNESS_IMPLEMENTATION_PLAN.md). These
documents distinguish target architecture from shipped behavior and define the
phase gates that prevent one Agent from implementing the entire programme in a
single unsafe change.

## What Pharos is

An integrated research platform covering the whole arc: discover → read →
translate → organise → write. Reading and translation is the flagship built
module; writing is roadmap.

It is **not** a translator with a library bolted on. That framing has caused
work to go in the wrong direction before.

## Repository map

```text
backend/        FastAPI, SQLAlchemy, SQLite. The authority for accounts,
                server-side paper records, jobs, AI chat, daily, discovery and
                projects. The local Zotero library remains desktop-authoritative.
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
    pharosReaderChat pharosDaily pharosDiscovery pharosProjects pharosProjectsApi pharosRail \
    pharosRailFooter pharosAdmin pharosProtocol pharosAuth pharosTheme \
    pharosStrings preferences_pharos preferences_pharos_daily
node test/check-locale-parity.js  # both locale files define the same ids
```

### The upstream baseline

The whole suite is **3364/3404** on a clean tree. Nobody had run it end to end
before, so that number is worth stating rather than discovering again:

```bash
cd client && test/runtests.sh          # all of it, ~30 minutes
```

The 40 failures are pre-existing and cluster into four causes, none of them
Pharos's: tests that need the network (retraction data, arXiv lookup, sync error
paths), tests that assume an `en-US` application locale (month names, plural
forms, `timestamp`), tests that need the window to be OS-active (tab traversal,
the citation dialog's bubbles and locators), and a handful in
`collectionViewItemTree` and `itemPane`. That last group was A/B'd against the
Pharos item-tree column specifically -- 124/129 with it and 124/129 without --
so the column is not the cause.

**`syncLocalTest` opens a modal that no stub catches**, and the run blocks until
somebody dismisses it by hand. It is upstream's: `syncLocal.js`, `syncLocalTest.js`
and `test/content/support.js` have never been edited in this repository. Budget
for it, or skip that file, but do not expect the full suite to run unattended.

For day-to-day work the Pharos suites plus `itemPane zoteroPane` are the gate;
the full run is for before a release.

### `itemPane` and `zoteroPane` are a RANGE, not a number

They score roughly **189-199 out of 203**, and which number you get is largely
luck. Four failures are hard and repeatable (bibliography-entry mode, `不详` vs
`n.d.`); the rest are focus and interaction tests that time out when the window
is not OS-active, and how many time out varies run to run on the same code.

This was written down after quoting "199/203, four known failures" for a long
time as though it were a baseline. It is the top of the range. Treating it as
stable makes every comparison against it unreliable in both directions -- a good
run hides a regression, and a bad run invents one.

So: a single run tells you almost nothing. To decide whether a change regressed
these suites, run the SAME suites three times with the change and three times
without it, and compare the ranges. Both A/B checks done that way here found the
change innocent and the variation to be noise -- including one where the version
WITHOUT the change scored lower.

Run it **without `-f`**. That flag stops at the first failure, so a suite with
four repeatable failures reports a misleading `5/6 tests passed -- aborting` and
looks like a fresh regression.

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
- **Version by the size of the change, not the size of the diff.** Desktop
  releases are `MAJOR.MINOR.PATCH`, tagged `desktop-v<version>`. A defect
  corrected is a PATCH bump; a new capability, changed behaviour, or a fix too
  large to call a bug fix is a MINOR bump. When it is genuinely unclear, take the
  MINOR — over-stating a change costs a moment's attention, under-stating it
  costs the user the chance to pay any. See [`docs/DECISIONS.md`](docs/DECISIONS.md)
  §13. The number lives in `client/version` and keeps its `.SOURCE` suffix.
