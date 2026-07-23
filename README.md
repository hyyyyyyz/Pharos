<div align="center">

<img src="assets/brand/wordmark.png" alt="Pharos" width="360" />

<br/>

**An integrated research platform — from reading papers to writing them, all under one light.**

**一体化科研平台 —— 从读到写，检索、翻译、精读、领航问答、每日跟进、文献管理、写作，尽在一盏灯下。**

> Named after the Pharos of Alexandria, the ancient lighthouse — a beam of
> light guiding readers through the fog of dense literature.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-0C2040.svg)](LICENSE)
&nbsp;![Status](https://img.shields.io/badge/status-active%20development-F8C040.svg)
&nbsp;![Backend](https://img.shields.io/badge/backend-FastAPI%20·%20SQLite-189090.svg)
&nbsp;![Frontend](https://img.shields.io/badge/frontend-React%20·%20Vite%20·%20TS-0C2040.svg)

</div>

---

## What it is

Pharos is an **integrated research platform** — one place for the whole arc of
research work: **discover → read → understand → organize → write**. Not a
reference manager with a translation plugin bolted on, and not "a PDF
translator" — it is built from the ground up as the workbench a researcher lives
in, with a clean client/server split so it grows in one place and ships to many:
browser today, desktop and phone later, all talking to **one backend**.

The **flagship module — and the proof the platform is real — is
layout-preserving reading & translation.** Drop in an English paper (PDF) and
get back a **Chinese version that keeps the original layout** — columns,
figures, tables and math stay exactly where they were; only the prose is
translated. You get both a **Chinese-only** and a **bilingual side-by-side**
PDF, in a reader you can zoom, pan, select, search and highlight. Around it sit
the library, the 领航 reading companion and the daily digest; **a writing
assistant is next** — reading and writing under the same light.

<div align="center">
<img src="assets/brand/poster.png" alt="Pharos — a workshop of small robots reading, translating and charting papers around a central lighthouse" width="620" />
</div>

## Highlights

- 📄 **Layout-preserving translation** — English → Chinese with columns,
  formulas, tables and figures kept in place. Mono + bilingual output. Powered
  by [BabelDOC](https://github.com/funstory-ai/BabelDOC) run at arm's length in
  its own process.
- 🔍 **A real reader, not an image viewer** — pdf.js with a true text layer:
  zoom, drag-to-pan, select & copy, in-document find, and passage highlights
  with notes. Coordinates are stored in PDF points, so a highlight lands right
  at any zoom or screen.
- 🧭 **领航 · AI reading companion** — a chat alongside the paper for asking
  about a paragraph instead of translating the whole thing. *(UI ready; wire an
  LLM key to enable.)*
- 📰 **每日论文 · daily arXiv digest** — a self-updating feed of new papers in
  the fields **you** define, each with a Chinese summary, key-point breakdown
  and relevance score. Keywords are per-user and editable; matching happens at
  read time, so editing a direction re-ranks your feed instantly.
- 📚 **A library that is actually yours** — Zotero-style category tree, item
  list and detail; collections, tags, full-text search (SQLite FTS5), and
  bibliographic metadata extracted from the PDF and reconciled against
  CrossRef / arXiv.
- 🔗 **Zotero, as a source not a silo** — link your Zotero account and its
  library syncs *into* Pharos; the 文库 is the home, Zotero is one way to fill it.
- 👤 **Multi-user from the core** — email + password accounts (argon2id, JWT),
  strict per-user isolation verified adversarially: another user's paper is
  indistinguishable from one that does not exist.
- 🎨 **Calm, brand-true theming** — a warm, paper-like palette drawn from the
  logo, light & dark, with ten selectable accents. Whole-PDF translation is an
  optional per-account setting — turn it off and the apparatus disappears
  rather than greying out.

## Architecture at a glance

```
Browser today · Desktop & mobile later    ── one thin client per platform
                    │  REST + Server-Sent Events, Bearer-token auth
                    ▼
        FastAPI backend  (the single core)
        accounts · library · jobs · daily · search · annotations · Zotero
                    │  arm's-length subprocess (NDJSON progress)
                    ▼
        Engine worker  →  BabelDOC (via pdf2zh-next)
                          mono.pdf (Chinese) + dual.pdf (bilingual)
```

The translation engine (BabelDOC) is **AGPL-3.0** and runs as a **separate
process in its own environment** — never imported in-process. That boundary is
also the AGPL aggregation seam and a quarantine for native dependencies. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full reasoning (including
the macOS Apple-Silicon `hyperscan` gotcha and the AGPL boundary).

**Stack.** Backend: FastAPI · SQLAlchemy 2.x · SQLite (WAL) · sse-starlette,
with ~400 tests. Frontend: React 18 · Vite · TypeScript (strict) · TanStack
Query · Zustand · pdf.js. Engine: BabelDOC via `pdf2zh-next`, in a Rosetta
`osx-64` conda env on Apple Silicon.

## Quick start (macOS / Apple Silicon)

> Requires: [conda](https://docs.conda.io/) (miniconda), Rosetta 2, Node 18+.

```bash
# 1. Set up the translation engine (isolated osx-64 / Rosetta conda env)
bash scripts/setup_engine_env.sh

# 2. Backend: install and run the API (SQLite is created on first run)
pip install -e backend
cp .env.example .env          # set PHAROS_AUTH_SECRET; add an LLM key when ready
python -m uvicorn pharos.main:app --host 127.0.0.1 --port 8848

# 3. Frontend: install and run the dev server (proxies /api to the backend)
cd frontend && npm install && npm run dev
```

Open `http://localhost:5173`, create an account, and drop in a PDF.

## Roadmap

- [x] Layout-preserving EN→ZH translation — mono + bilingual PDF
- [x] FastAPI core: upload, async translate jobs, live progress (SSE), library
- [x] React reader: zoom / pan / text layer / find / highlights & notes
- [x] Accounts, per-user isolation, and a split-panel sign-in
- [x] 每日论文 — daily arXiv digest with user-defined research directions
- [x] Full-text search, collections & tags, metadata extraction
- [x] Zotero Web API sync
- [ ] **领航 chat backend** — LLM Q&A over the paper *(needs an API key)*
- [ ] Translation quality: DeepSeek + scientific glossary prompt
- [ ] **写作助手 — writing assistant** — from outline to draft, grounded in your library
- [ ] **文献检索 — cross-database discovery** — search arXiv & journals, import in one click
- [ ] Public deployment (same-origin, HTTPS, httpOnly-cookie auth)
- [ ] Desktop (Tauri) & mobile clients on the same backend

## License

Pharos is licensed under the **GNU Affero General Public License v3.0**
([`LICENSE`](LICENSE)). This matches its AGPL-3.0 engine dependency and keeps
the whole project free and open. If you run a modified Pharos as a network
service, the AGPL requires you to offer your users its source.

## Acknowledgements

The layout-preserving translation core is powered by
[**BabelDOC**](https://github.com/funstory-ai/BabelDOC) and
[**PDFMathTranslate / pdf2zh-next**](https://github.com/PDFMathTranslate/PDFMathTranslate-next)
by funstory.ai (AGPL-3.0). Pharos builds its own application, library, reading
experience and daily digest around that engine.

<div align="center">
<br/>
<img src="assets/brand/mark.png" alt="Pharos mark" width="72" />
<br/>
<sub><b>Pharos</b> · 灯塔照亮文献之海</sub>
</div>
