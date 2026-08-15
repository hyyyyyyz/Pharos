# Pharos — Architecture

This document records the design of Pharos and the reasoning behind each
decision. It is the source of truth; code should follow it, and changes to the
design should update it.

## 1. Goal

**Hard requirement (MVP):** take an English paper PDF and produce a Chinese
translation that **completely preserves the original layout** — multi-column
flow, figures, tables, and mathematics stay in place; only the prose is
translated. Output both a Chinese-only (`mono`) PDF and a bilingual
side-by-side (`dual`) PDF.

**Long-term:** grow from a Zotero-derived desktop research client into an
evidence-first Research OS: discover and read papers, turn page-addressable
evidence into testable ideas, plan experiments, and—only after Decision 9 is
formally superseded—execute bounded experiments, then carry
verified claims into writing. The desktop client is primary and owns the local
research experience. The backend supplies model, translation, account, and
remote-companion capabilities; it is not the authority for the local Zotero
library. The detailed workflow lives in
[`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md), and the desktop data contract in
[`CLIENT_DATA_ARCHITECTURE.md`](CLIENT_DATA_ARCHITECTURE.md). The planned durable
execution layer for multi-step research automation is specified separately in
[`HARNESS_ARCHITECTURE.md`](HARNESS_ARCHITECTURE.md); it is not an implemented
capability merely because it is documented.

## 2. Engine choice

We do **not** re-implement layout-preserving PDF translation. It is a deep
problem (layout detection, formula/figure protection, paragraph reflow into the
original geometry), and a mature open-source engine already solves it.

- **Engine:** [BabelDOC](https://github.com/funstory-ai/BabelDOC) `0.6.x`.
- **We call it only through its sanctioned wrapper**
  [`pdf2zh-next`](https://github.com/PDFMathTranslate/PDFMathTranslate-next)
  `2.9.0` — specifically `pdf2zh_next.high_level.do_translate_async_stream`,
  which streams structured progress and, on completion, exposes
  `result.no_watermark_mono_pdf_path` and `result.no_watermark_dual_pdf_path`.
- We **never** `import babeldoc.*` directly — those APIs are internal/unstable.
  We pin `pdf2zh-next==2.9.0` and let it resolve its matching `babeldoc`.
- The engine is **ONNX-based** (DocLayout-YOLO). **No PyTorch, no CUDA.**
- Formulas / inline math are protected by placeholder tokens so the LLM never
  translates them.

MinerU / marker (structured extraction) are **not** the MVP engine. Current
ingestion uses PyMuPDF to populate full text and page-addressable chunks. A
future structured extractor belongs behind a separate extraction seam; it must
not be coupled to the `TranslationEngine` protocol.

## 3. Engine embedding: arm's-length subprocess

The engine runs as a **separate OS process in its own isolated engine environment**,
invoked by the backend via `asyncio.create_subprocess_exec`. A thin
`engine_worker` imports `do_translate_async_stream`, iterates the async
generator, and prints **one NDJSON line per event** (`progress` / `finish` /
`error`) to stdout. The backend parses that stream and republishes it over SSE.

Three reasons this is a subprocess, not an in-process import:

1. **Native-dep quarantine.** The engine drags in `hyperscan`, `onnxruntime`,
   and `pymupdf`. Keeping them in a separate environment prevents their native
   dependency constraints from leaking into the API environment. Production
   uses the Linux virtual environment at `/opt/pharos-engine`; Apple-Silicon
   source development uses the x86_64/Rosetta environment described in §5.
2. **Crash isolation.** A segfault in a native dep kills the worker, not the API.
3. **AGPL boundary (see §7).** A genuine arm's-length process is the recognized
   aggregation seam. **Never set `settings.basic.debug=True`** — it collapses the
   engine into the caller's process.

Guard `result.no_watermark_*` for `None` before storing (use `getattr`).

## 4. Components

```
client/                   Zotero-derived primary desktop client
   ├── Zotero library     shared zotero.sqlite, storage, items, annotations
   ├── Daily Vault        user-selected portable digest snapshot
   ├── reserved sidecar   pharos-local.sqlite path; no writer yet
   └── HTTPS              only when a service capability is needed
frontend/                 React 18 + Vite + TypeScript, pdf.js
   │  authenticated REST, streamed AI replies, and job polling
backend/                  FastAPI + Uvicorn
   ├── api/               papers, jobs, chat, daily, discovery, projects, evidence
   ├── services/          library, translation, search, discovery, research projects
   ├── engines/           TranslationEngine protocol + BabelDocEngine
   ├── db/                SQLAlchemy 2.x models over SQLite (WAL, busy_timeout)
   └── storage/           content-addressed blob store: files/<sha256>/{original,mono,dual}.pdf
backend/engine_worker/    separate engine environment/process; emits NDJSON progress
```

Zotero, Vibero, and Pharos may use one Zotero library in turn, not
simultaneously. Pharos keeps an independent application profile and never adds
private schema to `zotero.sqlite`. See the client data document for schema
compatibility and release gates.

The research v1 is implemented in the same FastAPI application: discovery
adapters query arXiv/OpenAlex, normalise and deduplicate results, and project
services persist the user's sources and stage records in SQLite. It deliberately
does not introduce Dify, LangGraph, Neo4j, or another workflow control plane.
That statement describes the **current v1 implementation**, not the target
architecture. Decision 16 now authorises a Pharos-native Research Harness as a
future additive control plane. The current stage flow remains user-driven until
each Harness phase passes its explicit gate; future automated stages must remain
explicit, persisted, bounded, and independently testable.

### Request flow

1. `POST /papers` — upload; hash (sha256) and store the PDF; PyMuPDF reads page
   count + a thumbnail.
2. `POST /papers/{id}/translate` — returns **`202` + `job_id` immediately**; a
   bounded asyncio worker pool drives one engine subprocess per job and pushes
   events onto a per-job `asyncio.Queue`. Job status/progress is persisted to
   SQLite so a browser refresh can re-attach to the latest snapshot. A backend
   restart can read that snapshot but **cannot resume the lost execution**;
   durable leases and recovery belong to the planned Research Harness.
3. `GET /jobs/{job_id}/events` — an optional **SSE** (`text/event-stream`)
   progress endpoint with heartbeat comments. Current web and desktop clients
   poll persisted job state instead.
4. `GET /papers/{id}/pdf/{original|mono|dual}` — serve the PDFs.

A translation takes **2–4 minutes** — it must **never** run inside a request
handler.

### Research v1 flow

1. `POST /discovery/search` queries selected arXiv/OpenAlex providers, merges
   duplicate records, interleaves source results, performs clearly-labelled
   title/abstract rule extraction, and persists the search plus per-provider
   errors.
2. `POST /discovery/results/{id}/analyze` optionally upgrades one result with a
   validated LLM reading of its title/abstract. It records the model but remains
   explicitly abstract-only and leaves the rule result intact on failure.
3. A user saves selected `LiteratureResult` rows into a `ResearchProject` as
   `ProjectSource` rows. The source note records why the paper belongs and what
   still needs checking.
4. The user advances the project through nine research stages and persists
   `ProjectArtifact` records for hypotheses, experiment plans, results, claims,
   drafts, and reviews.

The final step records human research work; it does **not** execute experiments
or independently verify a result. See
[`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md) for the exact capability boundary.

## 5. macOS / Apple-Silicon reality (the `hyperscan` gotcha)

BabelDOC hard-depends on `hyperscan`, which publishes **no macOS arm64 wheels**
and **no cp313 wheels for any platform**. So a naive
`conda create python=3.13 && pip install` on Apple Silicon **fails** at a
from-source build — and downgrading Python alone does **not** fix it (it is an
architecture problem, not a Python-version problem).

**Solution:** create the engine env under `CONDA_SUBDIR=osx-64` (Rosetta 2) with
Python 3.12, so the prebuilt **x86_64** wheels for hyperscan / onnxruntime /
pymupdf install with zero compiling. Translation is network/LLM-bound, so the
Rosetta overhead is negligible. This is automated in
[`scripts/setup_engine_env.sh`](../scripts/setup_engine_env.sh).

On **Linux x86_64**, including the production container, hyperscan has native
wheels and the image installs the engine in `/opt/pharos-engine`; no Rosetta or
conda assumption belongs to the production component contract. Windows x86_64
source environments likewise do not need the Apple-Silicon workaround.

First run downloads a ~75 MB DocLayout-YOLO model + fonts from HuggingFace;
pre-cache with a warmup (or `--generate-offline-assets` / `--restore-offline-assets`)
and set `HF_HOME` / `HF_ENDPOINT` for CN / air-gapped networks.

## 6. Translation backend

Any **OpenAI-compatible** endpoint, configured (not hard-coded) via
`base_url` / `api_key` / `model`:

- **DeepSeek-chat** — default; natively OpenAI-compatible, cheap, good Chinese.
- **Claude / OpenAI / Qwen / Moonshot / Zhipu** — via their OpenAI-compatible
  endpoints or a LiteLLM shim.
- **Ollama (local)** — fully offline fallback.

For quality we control the system prompt (scientific EN→ZH, keep `$...$` and
`<b0>..</b0>` placeholders verbatim, temperature 0) and feed a per-job
`source,target` glossary CSV for terminology consistency.

## 7. License

The engine stack (BabelDOC, pdf2zh-next) is **AGPL-3.0**. Pharos is therefore
also **AGPL-3.0** — the clean, conflict-free choice for an open-source project.

- **Private, single-user, non-networked use:** zero source-disclosure obligation.
- **Distribution OR exposing it to other users over a network** triggers AGPL:
  every network user must be offered the Corresponding Source of the AGPL work
  (engine + our modifications + anything forming one combined work).

Because Pharos is open-source under AGPL-3.0, this is satisfied by keeping the
repo public. The subprocess boundary is retained regardless, as good hygiene and
to keep future licensing options open. A proprietary/hosted product would need a
funstory-ai commercial license.

## 8. Data model

Pharos has three deliberately separate ownership domains.

### Zotero desktop library

Zotero's schema remains authoritative for items, collections, creators, tags,
attachments, PDFs, notes, annotations, saved searches, citation data, and sync
state. The Pharos desktop client uses Zotero's own data model and transactions;
it does not mirror this graph through the Local API.

### Reserved Pharos desktop sidecar

`Zotero.Pharos.SharedLibrary.sidecarPath()` currently reserves
`<Zotero data directory>/pharos-local.sqlite`, but no feature opens or writes
that file yet. Current AI conversations, paper profiles, translation tasks,
research workflow records, and the Daily Papers online working copy live in the
optional backend. Daily Papers also has a separate, versioned, user-selected
Vault for portable backup and recovery; it is not a second live database. Any
future local sidecar writer must introduce an explicit schema, migration,
backup, and `(libraryID, key)` identity contract before this document may
describe those records as local.

### Optional backend and web companion

- `User` / `ZoteroLink` — account isolation, preferences, and optional Zotero
  credentials/sync state.
- `Paper(id, user_id, title, authors, source[upload|arxiv], arxiv_id,
  source_lang, full_text, bibliographic fields, added_at, orig_sha256,
  page_count)`
- `PaperChunk(paper_id, user_id, page_no, ordinal, text, char_start, char_end,
  extraction_version)` — the current page-addressable extraction substrate.
- `TranslationJob(id, paper_id, status, engine, target_lang, progress, stage,
  mono_path, dual_path, error, started_at, finished_at)`
- `Collection` / `Tag` / `Highlight` / `Note` — owner-scoped organisation and
  reader annotations.
- `PaperAiContext` / `AiConversation` / `AiMessage` — reusable paper
  understanding and durable, owner-scoped chat history.
- `Evidence(paper_id, project_id, chunk_id, kind, locator, page_no, rects,
  text, statement, provenance fields)` — page-addressable evidence with an
  explicit distinction between quotes, notes, rule summaries, and model
  inferences.
- `DailyPaper` / `DailyRun` / `UserDirection` / `UserDailyConfig` — shared paper
  fetch/reading data plus per-user feed matching and settings.
- `ResearchProject(id, user_id, name, description, research_question, status,
  stage, created_at, updated_at)`
- `LiteratureSearch(id, user_id, project_id, query, sources, status, errors,
  result_count, created_at, completed_at)`
- `LiteratureResult(search_id, bibliographic fields, source_ids, rank,
  analysis_mode, analysis_model, summary_zh, contribution, core_trick, method,
  results, limitations)`
- `ProjectSource(project_id, result_id, note, added_at)` — explicit admission to
  a project plus the researcher's evidence/rationale note.
- `ProjectArtifact(project_id, stage, type, title, body, status)` — durable
  human-authored research records, not proof that an automated experiment ran.

Page-addressable `PaperChunk` and `Evidence` rows now implement the first
evidence vertical slice. Automatic Idea review, experiment execution, grounded
answer citations, and Claim bindings remain future work; they are not implied
by the current generic artifact row. Their ordering and evidence contract are
defined in [`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md).

## 9. Extensibility seams

- Current ingestion uses PyMuPDF to populate `Paper.full_text` and
  page-addressable `PaperChunk` rows. A future structured extractor such as
  MinerU needs a separate extraction seam; translation APIs remain unchanged.
- Current `PaperChunk` rows + a future vector index (sqlite-vec / FAISS) →
  grounded RAG/Q&A over the existing Evidence Ledger.
- pdf.js text layer + `Highlight`/`Note` → coordinate-anchored annotations.
- The desktop client is built from Zotero source. Its local reference library is
  usable without the backend; translation, model-backed tasks, and synchronized
  Pharos records cross the REST API when needed.
- Discovery providers sit behind normalising adapters. A provider can fail while
  the run persists successful results from another provider as `partial`.
- Default result analysis is deterministic extraction from title/abstract and is
  labelled `rules`. A user may request a validated model reading, labelled `llm`
  with its model name. Both are abstract-only and must never be presented as a
  full-paper reading.
- Future model-backed stages must validate typed output and store concise
  rationale/source bindings, never raw chain-of-thought.
- Novelty is a search report, not a truth oracle: the product may report
  `likely_distinct`, `likely_overlap`, or `search_incomplete`, but never claim
  that an external search has confirmed originality.
- SQLite remains the source of truth until measured requirements justify a
  vector index or graph store. Adding retrieval infrastructure must not leak a
  user's papers, profile, notes, ideas, or experiment history across accounts.

## 10. Planned Research Harness

Daily Papers, Literature Discovery, and Project Research currently use three
different execution shapes. Their future automation converges on one additive,
durable Harness whose database records Run, Step, Attempt, Event, Artifact,
Approval, lease, and usage state. Existing domain tables remain authoritative;
an explicit idempotent publish step materialises accepted Harness output into
`DailyPaper`, `LiteratureResult`, `Evidence`, or `ProjectArtifact`.

The Harness follows one load-bearing rule:

> Workflow controls order, authority, budget, recovery, and publication;
> an Agent may make decisions only inside one typed, bounded Step.

The target design, open-source comparison, concrete business workflows, staged
delivery gates, and implementation hand-off are maintained in:

- [`HARNESS_ARCHITECTURE.md`](HARNESS_ARCHITECTURE.md)
- [`HARNESS_LANDSCAPE.md`](HARNESS_LANDSCAPE.md)
- [`HARNESS_WORKFLOWS.md`](HARNESS_WORKFLOWS.md)
- [`HARNESS_IMPLEMENTATION_PLAN.md`](HARNESS_IMPLEMENTATION_PLAN.md)

H0-H6 do not grant Agents shell access, direct access to `zotero.sqlite`, or the
ability to execute experiments. Decision 9 remains in force. A future experiment
runtime requires a separate decision and sandbox architecture before it can be
planned as implementation work.
