<div align="center">

# Pharos

**An integrated research platform — a lighthouse over the sea of literature.**
**一体化科研平台 —— 检索、阅读、翻译、AI 领航问答，尽在一处。**

> Named after the Pharos of Alexandria, the ancient lighthouse — a beam of
> light guiding readers through the fog of dense literature.

*Status: early development — the layout-preserving paper translator & reader is the first module.*

</div>

---

## What it is

Drop in an English paper (PDF) and get back a **Chinese version that keeps the
original layout** — columns, figures, tables, and math stay exactly where they
were; only the prose is translated. You get both a **Chinese-only** PDF and a
**bilingual side-by-side** PDF.

Unlike a reference manager (Zotero) bolted together with translation plugins,
Pharos is built from the ground up as a **read + translate** product, with a
clean client/server split so it can grow — AI summary/Q&A, highlights & notes,
a terminology glossary, arXiv import — and later ship as a desktop app (macOS /
Windows) or a phone client, all talking to **one backend**.

## Architecture at a glance

```
Browser / Desktop (Tauri) / Mobile  ── one thin client per platform
                    │  REST + Server-Sent Events
                    ▼
        FastAPI backend  (the single core)
        library · jobs · glossary · TranslationEngine abstraction
                    │  arm's-length subprocess (NDJSON progress)
                    ▼
        Engine worker  →  BabelDOC (via pdf2zh-next)
                          mono.pdf (Chinese) + dual.pdf (bilingual)
```

The translation engine (BabelDOC) is **AGPL-3.0** and is run as a **separate
process in its own environment** — never imported in-process. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and the
reasoning behind every choice (including the macOS Apple-Silicon `hyperscan`
gotcha and the AGPL boundary).

## Quick start (macOS / Apple Silicon)

> Requires: [conda](https://docs.conda.io/) (miniconda), Rosetta 2, Node 18+.

```bash
# 1. Set up the translation engine (isolated osx-64 / Rosetta conda env)
bash scripts/setup_engine_env.sh

# 2. (coming) set up the backend app env and run it
# 3. (coming) run the frontend
```

## Roadmap

- [ ] **M0** — Engine install spike: English PDF → layout-preserving Chinese PDF (CLI proof)
- [ ] **M1** — Engine worker + `TranslationEngine` abstraction
- [ ] **M2** — FastAPI core: upload, translate (async jobs), live progress (SSE), library
- [ ] **M3** — React reader: library grid + split-pane original/translation
- [ ] **M4** — Terminology glossary + scientific EN→ZH prompt
- [ ] **M5** — Offline model assets, cancellation, polish
- [ ] Later — AI summary/Q&A (RAG), highlights & notes, arXiv import, desktop & mobile clients

## License

Pharos is licensed under the **GNU Affero General Public License v3.0**
([`LICENSE`](LICENSE)). This matches its AGPL-3.0 engine dependency and keeps
the whole project free and open.

## Acknowledgements

The layout-preserving translation core is powered by
[**BabelDOC**](https://github.com/funstory-ai/BabelDOC) and
[**PDFMathTranslate / pdf2zh-next**](https://github.com/PDFMathTranslate/PDFMathTranslate-next)
by funstory.ai (AGPL-3.0). Pharos builds its own application, library, and
reading experience around that engine.
