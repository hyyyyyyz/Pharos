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

**Long-term:** grow from a research-reading assistant into an evidence-first
Research OS: discover and read papers, turn page-addressable evidence into
testable ideas, plan and execute bounded experiments, and carry verified claims
into writing. It remains one backend serving web, desktop (macOS/Windows), and
mobile clients. The detailed workflow, schemas, checkpoints, and safety contract
live in [`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md).

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

MinerU / marker (structured extraction) are **not** the MVP engine. They become
useful later to populate text chunks for RAG/Q&A, added behind the same
`TranslationEngine` seam without disturbing the API or UI.

## 3. Engine embedding: arm's-length subprocess

The engine runs as a **separate OS process in its own conda environment**,
invoked by the backend via `asyncio.create_subprocess_exec`. A thin
`engine_worker` imports `do_translate_async_stream`, iterates the async
generator, and prints **one NDJSON line per event** (`progress` / `finish` /
`error`) to stdout. The backend parses that stream and republishes it over SSE.

Three reasons this is a subprocess, not an in-process import:

1. **Native-dep quarantine.** The engine drags in `hyperscan`, `onnxruntime`,
   and `pymupdf` built for **x86_64 / Rosetta** (see §5). Keeping them in a
   separate env leaves the backend env a clean native-arm64 Python 3.13.
2. **Crash isolation.** A segfault in a native dep kills the worker, not the API.
3. **AGPL boundary (see §7).** A genuine arm's-length process is the recognized
   aggregation seam. **Never set `settings.basic.debug=True`** — it collapses the
   engine into the caller's process.

Guard `result.no_watermark_*` for `None` before storing (use `getattr`).

## 4. Components

```
frontend/                 React 18 + Vite + TypeScript, pdf.js
   │  REST + SSE
backend/                  FastAPI + Uvicorn (native arm64, Python 3.13)
   ├── api/               routers (papers, jobs, daily, discovery, projects)
   ├── services/          library, translation, search, discovery, research projects
   ├── engines/           TranslationEngine protocol + BabelDocEngine
   ├── db/                SQLAlchemy 2.x models over SQLite (WAL, busy_timeout)
   └── storage/           content-addressed blob store: files/<sha256>/{original,mono,dual}.pdf
backend/engine_worker/    runs in the osx-64 engine env; emits NDJSON progress
```

The research v1 is implemented in the same FastAPI application: discovery
adapters query arXiv/OpenAlex, normalise and deduplicate results, and project
services persist the user's sources and stage records in SQLite. It deliberately
does not introduce Dify, LangGraph, Neo4j, or another workflow control plane.
The current stage flow is user-driven; future automated stages must remain
explicit, persisted, and independently testable.

### Request flow

1. `POST /papers` — upload; hash (sha256) and store the PDF; PyMuPDF reads page
   count + a thumbnail.
2. `POST /papers/{id}/translate` — returns **`202` + `job_id` immediately**; a
   bounded asyncio worker pool drives one engine subprocess per job and pushes
   events onto a per-job `asyncio.Queue`. Job status/progress is persisted to
   SQLite so a browser refresh (or worker restart) can re-attach.
3. `GET /jobs/{job_id}/events` — an **SSE** (`text/event-stream`) stream with
   heartbeat comments.
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

On **Linux / Windows x86_64** (e.g. a future always-on backend), hyperscan has
native wheels — the engine installs with no Rosetta dance at all.

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

- `User` / `ZoteroLink` — account isolation, preferences, and optional Zotero
  credentials/sync state.
- `Paper(id, user_id, title, authors, source[upload|arxiv], arxiv_id,
  source_lang, full_text, bibliographic fields, added_at, orig_sha256,
  page_count)`
- `TranslationJob(id, paper_id, status, engine, target_lang, progress, stage,
  mono_path, dual_path, error, started_at, finished_at)`
- `Collection` / `Tag` / `Highlight` / `Note` — owner-scoped organisation and
  reader annotations.
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

Future page-addressable evidence, automatic Idea review, experiment execution,
and Claim bindings require new entities; they are not implied by the current
generic artifact row. Their ordering and evidence contract are defined in
[`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md).

## 9. Extensibility seams

- `TranslationEngine` protocol → a future `MinerUEngine` can also populate
  page-addressable `PaperChunk` rows, with translation APIs unchanged.
- Future `PaperChunk` + a vector index (sqlite-vec / FAISS) → grounded RAG/Q&A
  and Evidence Ledger over papers.
- pdf.js text layer + `Highlight`/`Note` → coordinate-anchored annotations.
- The desktop client is built from Zotero source and talks to the same REST
  API as the web client; SQLite + on-disk blobs make the whole library portable.
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
