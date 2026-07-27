<p align="center">
  <strong>English</strong> · <a href="./README_CN.md">Simplified Chinese</a>
</p>

<div align="center">

<img src="assets/brand/wordmark.png" alt="Pharos" width="360" />

### An integrated, evidence-first research workbench

Discover literature, read and translate papers, organize evidence, and carry an
idea through a durable research workflow — in one self-hosted platform.

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-0C2040.svg)](LICENSE)
&nbsp;![Status](https://img.shields.io/badge/status-active%20development-F8C040.svg)
&nbsp;![Backend](https://img.shields.io/badge/backend-FastAPI%20·%20SQLite-189090.svg)
&nbsp;![Clients](https://img.shields.io/badge/clients-React%20·%20Tauri-0C2040.svg)

[Official Website](https://hyyyyyyz.github.io/Pharos/) ·
[Web App](https://pharos.selab.top/) ·
[Architecture](docs/ARCHITECTURE.md) ·
[Research workflow](docs/RESEARCH_WORKFLOW.md) ·
[Contributing](CONTRIBUTING.md)

</div>

---

## What is Pharos?

Pharos is an open-source research platform for the path from a question to a
defensible research output. It brings literature discovery, layout-preserving
paper translation, deep reading, annotations, personal library management,
daily research monitoring, Zotero sync, and persistent research projects into
one workbench.

It is **not only a PDF translator**. Translation is an important foundation,
but the product is organized around the wider research loop:

```text
discover → screen → read → organize → hypothesize → plan → record → claim → draft → review
```

The public [GitHub Pages site](https://hyyyyyyz.github.io/Pharos/) is the
marketing website. The actual product consists of the React web client (or the
Tauri desktop shell), the FastAPI backend, SQLite storage, and an isolated PDF
translation worker.

<div align="center">
  <img src="assets/brand/poster.png" alt="Small research robots reading and organizing papers around the Pharos lighthouse" width="620" />
</div>

## What works today

The four primary modules are functional and backed by persistent server-side
data rather than static mockups.

| Module | Current capabilities |
| --- | --- |
| **Library** | Import PDFs, preserve bibliographic metadata, translate papers, search full text, organize collections, annotate passages, attach notes to highlights, and import bibliographic metadata from Zotero. |
| **Daily Papers** | Follow user-defined research directions, fetch new arXiv papers, rank them for each user, optionally generate model-backed abstract readings, and import useful papers into the library. |
| **Literature Discovery** | Search arXiv and OpenAlex, merge duplicate records, retain partial results when a provider fails, reopen search history, inspect concise core-trick summaries, and save selected sources to a project. |
| **Research Projects** | Maintain research questions, source-selection rationale, a nine-stage project state, and durable hypothesis, experiment-plan, result, claim, draft, and review records. |

### Reading and translation

- **Layout-preserving English-to-Chinese translation.** BabelDOC, invoked
  through `pdf2zh-next`, produces a Chinese-only PDF and a bilingual PDF while
  aiming to retain the original columns, figures, tables, and mathematics.
- **A real PDF reader.** The React client uses pdf.js with a text layer, zoom,
  drag-to-pan, text selection, copy, in-document search, and annotations.
- **Coordinate-stable highlights.** Highlight locations are stored in PDF
  coordinates so they remain attached at different zoom levels and window sizes.
- **Optional translation providers.** Keyless Bing/Google translation and
  configured DeepSeek/OpenAI-compatible providers share the same engine boundary.
- **Per-account controls.** Whole-document translation can be disabled without
  hiding previously generated translated files.

### Discovery and research records

- Search arXiv and OpenAlex in parallel and normalize them into one result model.
- De-duplicate by DOI or normalized title and preserve all contributing sources.
- Keep successful results when one provider is unavailable and persist the
  provider-specific failure.
- Show deterministic title/abstract extraction by default, with an optional
  schema-validated model reading that is explicitly labelled as abstract-only.
- Save a paper to a research project together with the reason it belongs there
  and what still needs verification.
- Move a project through nine explicit stages:

```text
discovery → ideation → planning → experimentation → analysis
          → claims → drafting → review → complete
```

The current workflow stores researcher-owned records. It does **not** claim to
run experiments, reproduce results, verify claims, or write a complete paper
autonomously. Those capabilities require stronger evidence and execution
contracts and remain future work.

### Library, accounts, and integrations

- Multi-user email/password accounts with Argon2id password hashing and signed
  bearer tokens.
- Owner-scoped papers, searches, projects, annotations, directions, and Zotero
  credentials.
- SQLite FTS5 full-text search and nested collection organization.
- PDF metadata extraction with Crossref/arXiv reconciliation where possible.
- One-way Zotero Web API metadata sync. A server with a registered Zotero OAuth
  application offers one-click browser authorization; manual API-key linking
  remains available as a fallback. Pharos does not download Zotero attachments
  or write translation/reading state back to Zotero.
- An experimental Tauri 2 desktop shell for macOS and Windows that reuses the
  exact React UI and connects to the same separately running backend.

### Connect Zotero

For a self-hosted deployment, register a web application at
[Zotero OAuth Apps](https://www.zotero.org/oauth/apps) with:

- Website: `https://pharos.selab.top/` (replace this with your own public URL)
- Callback: `https://pharos.selab.top/api/zotero/oauth/callback`

Set the OAuth client key and secret only on the backend. When they are not
configured, the account settings keep the manual Zotero user-ID/API-key flow
available. Both connection methods grant Pharos a one-way, metadata-only import
path for the user's personal library; attachments, group libraries, notes, and
write-back are outside the current integration.

## Architecture

<div align="center">
  <img src="assets/brand/architecture-overview.png" alt="Pharos clients connect to one FastAPI core, which delegates PDF translation to an isolated BabelDOC worker" width="100%" />
</div>

The diagram above focuses on the PDF translation execution path. The current
repository also contains Literature Discovery, Research Projects, and an
experimental Tauri desktop shell.

Pharos keeps one backend as the source of truth for every client.

```text
React web client / Tauri desktop client
                  │
                  │ REST + Server-Sent Events + bearer-token authentication
                  ▼
            FastAPI application
 accounts · library · jobs · daily · discovery · projects · annotations · Zotero
                  │
                  │ arm's-length subprocess · NDJSON progress
                  ▼
      engine worker → pdf2zh-next → BabelDOC
                    → mono PDF + bilingual PDF
```

- **Backend:** FastAPI, SQLAlchemy 2.x, SQLite in WAL mode, SSE, a
  content-addressed PDF blob store, and background job managers.
- **Web client:** React 18, TypeScript, Vite, TanStack Query, Zustand, and pdf.js.
- **Desktop client:** Tauri 2 with the same frontend bundle; the backend and
  translation engine remain separate services.
- **Translation boundary:** BabelDOC runs in its own Python environment and OS
  process. The backend consumes NDJSON progress and republishes it over SSE.
- **External sources:** arXiv, OpenAlex, Crossref, Zotero, and optional
  OpenAI-compatible model providers.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the engine boundary,
storage model, request flow, licensing considerations, and the Apple-Silicon
`hyperscan` workaround.

## Repository layout

```text
Pharos/
├── backend/                 FastAPI core, services, database, tests
│   ├── pharos/              API, domain services, storage, engine adapter
│   └── engine_worker/       isolated BabelDOC worker; emits NDJSON progress
├── frontend/                React web product and PDF reader
├── desktop/                 Tauri 2 desktop shell and release configuration
├── site/                    Three.js/Vite GitHub Pages marketing site
├── scripts/                 environment and engine setup utilities
├── docs/                    architecture and research-workflow specifications
└── assets/brand/            shared logos, poster, and architecture artwork
```

The `site/` project is independent of the research application. Running the
marketing site does not start the FastAPI backend or the Pharos product UI.

## Run Pharos locally

### Requirements

- Python **3.11 or newer**
- Node.js **20 or newer** and npm
- A modern browser
- For PDF translation on macOS Apple Silicon: conda and Rosetta 2
- For the desktop shell: Rust **1.77.2 or newer** and the platform build tools

The documented engine bootstrap targets macOS Apple Silicon. Linux and Windows
x86_64 have native engine wheels, but their full installation path is not yet
wrapped by a repository setup script.

### 1. Clone and configure

```bash
git clone https://github.com/hyyyyyyz/Pharos.git
cd Pharos
cp .env.example .env
```

Generate a signing secret and place it in `.env` as `PHAROS_AUTH_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The backend can generate an ephemeral secret for localhost development, but a
configured secret keeps sessions valid across restarts and is mandatory before
exposing the API beyond localhost.

### 2. Prepare the translation engine (macOS Apple Silicon)

```bash
bash scripts/setup_engine_env.sh
```

The script creates an isolated `osx-64` conda environment named
`pharos-engine`, installs `pdf2zh-next==2.9.0` and BabelDOC, and verifies the
native dependencies under Rosetta. If conda is installed somewhere other than
`~/miniconda3`, set `PHAROS_ENGINE_PYTHON` to the absolute interpreter path.

You may skip this step while working only on library, discovery, project, or UI
features; translation jobs will require the worker environment.

### 3. Start the backend

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "backend[dev]"
python -m uvicorn pharos.main:app --host 127.0.0.1 --port 8848 --reload
```

On Windows, activate the environment with `.venv\Scripts\activate`.

The database and blob directories are created under `data/` on first start.
Check the API at `http://127.0.0.1:8848/api/health`.

### 4. Start the web client

In a second terminal:

```bash
npm ci --prefix frontend
npm --prefix frontend run dev
```

Open `http://localhost:5173`. The Vite development server proxies `/api` to
`http://127.0.0.1:8848`.

## Run the desktop client

The desktop application is a thin Tauri shell. It uses the same React frontend
and expects the backend to be running separately.

```bash
npm ci --prefix frontend
npm ci --prefix desktop
npm --prefix desktop run dev
```

Production builds use `frontend/.env.desktop` (or the corresponding process
environment) for `VITE_API_BASE`. The release workflow can build universal
macOS installers and Windows MSI/NSIS installers, but current packages are
unsigned. See [`desktop/README.md`](desktop/README.md).

## Develop the marketing site

The public website is a separate static Vite/Three.js project and does not need
Python, the API, a database, or model keys.

```bash
npm ci --prefix site
npm --prefix site run dev -- --port 5174
```

Use port `5174` when the product frontend is already using `5173`.

```bash
npm --prefix site run build
npm --prefix site run preview
```

## Configuration

The backend reads the repository-root `.env` file. The most important settings
are:

| Variable | Purpose |
| --- | --- |
| `PHAROS_AUTH_SECRET` | Signs access tokens. Use at least 32 random characters for any persistent or networked instance. |
| `PHAROS_CREDENTIAL_SECRET` | Independently encrypts stored Zotero credentials and temporary OAuth secrets. Use at least 32 random characters and do not reuse the auth or OAuth secret. |
| `PHAROS_CREDENTIAL_SECRET_PREVIOUS` | Optional previous credential-encryption secret used temporarily during key rotation. |
| `PHAROS_ZOTERO_OAUTH_CLIENT_KEY` | Server-side key from the registered Zotero OAuth application. |
| `PHAROS_ZOTERO_OAUTH_CLIENT_SECRET` | Server-side secret from the registered Zotero OAuth application; never expose it through a `VITE_*` variable or commit it. |
| `PHAROS_ZOTERO_OAUTH_CALLBACK_URL` | Exact public callback registered with Zotero, for example `https://pharos.selab.top/api/zotero/oauth/callback`. |
| `PHAROS_ZOTERO_OAUTH_RETURN_URL` | Fixed product URL used after the callback completes, for example `https://pharos.selab.top/`. |
| `PHAROS_DATA_DIR` | Overrides the SQLite database and PDF blob root. Defaults to `data/`. |
| `PHAROS_ENGINE_PYTHON` | Absolute path to the Python interpreter inside the isolated translation-engine environment. |
| `PHAROS_TRANSLATOR_TYPE` | `bing`, `google`, `deepseek`, `openai`, or `custom`. |
| `PHAROS_CHAT_PROVIDER` | Selects the configured provider used by optional model-backed reading tasks. |
| `PHAROS_DEEPSEEK_*`, `PHAROS_OPENAI_*`, `PHAROS_CUSTOM_*` | API key, base URL, and model for named OpenAI-compatible providers. |
| `PHAROS_CORS_ORIGINS` | Comma-separated allowed web origins. Set explicit origins in a real deployment. |

The default translator is keyless Bing. Model-backed daily reading and
abstract analysis remain unavailable until a usable provider key and model are
configured; the rest of the application continues to work without one.

## API basics

- The API base path is `/api`.
- `POST /api/auth/register` and `POST /api/auth/login` issue bearer tokens.
- Business endpoints require `Authorization: Bearer <token>`; `/api/health` and
  the authentication entry points are the main public exceptions. The Zotero
  OAuth callback is also public because the browser redirect cannot carry the
  bearer token; it is protected by a short-lived, one-use server flow and a
  secure browser cookie.
- Translation progress is available from
  `GET /api/jobs/{job_id}/events` as an authenticated SSE stream.
- FastAPI exposes interactive OpenAPI documentation at `http://127.0.0.1:8848/docs`
  and the schema at `/openapi.json`.

Representative endpoint groups include `/api/papers`, `/api/search`,
`/api/daily`, `/api/discovery`, `/api/projects`, `/api/collections`,
`/api/highlights`, and `/api/zotero`.

## Verification

From the repository root:

```bash
# Backend tests
python -m pytest backend/tests

# Product and marketing builds
npm --prefix frontend run build
npm --prefix site run build

# Desktop Rust shell
cargo check --manifest-path desktop/src-tauri/Cargo.toml
```

The live BabelDOC integration test is optional because it requires the isolated
engine environment and a real PDF fixture.

## Current boundaries and next directions

Pharos deliberately distinguishes implemented records from automated research:

- The **Navigator** reading-companion interface exists, but its streaming paper
  Q&A backend is not yet mounted in the FastAPI application.
- Literature Discovery reads search metadata and abstracts, not full papers.
- Research Projects persist plans and results supplied by the researcher; they
  do not run code, allocate GPUs, or validate metrics.
- A `verified` project record is a user decision, not independent reproduction.
- Tags, paper-level notes, direct arXiv-link import, and one-click
  Discovery-to-Library download are not yet complete end-to-end frontend flows.
- Zotero sync is metadata-only and one-way into Pharos. One-click authorization
  additionally requires the deployment owner to register and configure a
  Zotero OAuth application; manual API-key linking remains available.
- The full product backend is currently self-hosted; GitHub Pages hosts only
  the public marketing site.
- The desktop shell exists, but signed/notarized public releases and a mobile
  thin client remain future work.

The next major workstreams are page-addressable evidence, grounded paper Q&A,
an evidence-aware idea workflow, sandboxed experiment execution, claim-to-result
bindings, and an evidence-constrained drafting/review pipeline. The detailed
contract is recorded in [`docs/RESEARCH_WORKFLOW.md`](docs/RESEARCH_WORKFLOW.md).

## License

Pharos is licensed under the **GNU Affero General Public License v3.0 or later**.
See [`LICENSE`](LICENSE). If you offer a modified Pharos to users over a network,
the AGPL requires those users to be offered the corresponding source code.

## Acknowledgements

Layout-preserving translation is powered by
[BabelDOC](https://github.com/funstory-ai/BabelDOC) and
[PDFMathTranslate / pdf2zh-next](https://github.com/PDFMathTranslate/PDFMathTranslate-next),
both maintained by funstory.ai and distributed under AGPL-3.0.

<div align="center">
  <br />
  <img src="assets/brand/mark.png" alt="Pharos lighthouse mark" width="72" />
  <br />
  <sub><strong>Pharos</strong> · A clear line through the literature</sub>
</div>
