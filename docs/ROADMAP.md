# Roadmap

Where Pharos is, where it is going, and what it deliberately will not do.

The non-goals are the important half. A roadmap that only lists goals cannot
stop two contributors building in opposite directions; the list of things we
have decided *against* can. If a piece of work looks like it belongs in the
"Not doing" section, raise it before writing code rather than after.

Reasons behind the structural choices live in [`DECISIONS.md`](DECISIONS.md).

---

## Now

### Built and in use

**Backend** — accounts (argon2id, JWT, token-epoch revocation), content-addressed
paper storage, BabelDOC translation jobs with SSE progress, FTS5 full-text
search, collections and tags, per-paper AI chat with reusable contexts, the daily
arXiv digest with per-user directions, literature discovery over arXiv and
OpenAlex, research projects, an admin console, and one-way Zotero Web API import.

**Web client** — the reading and library UI, the admin console, settings, daily
papers, discovery, and projects.

**Desktop client** (`client/`, built from Zotero source) — the primary product
surface, retaining Zotero's library,
reader, annotations, citation styles and translators, plus:

| Feature | Where it lives |
| --- | --- |
| Layout-preserving translation | PDF Reader toolbar left of Find, or the library PDF item's right-click menu; both offer Translated Only and Side by Side |
| AI chat about the open paper | Full-height default right-hand reader pane; the sidenav still reaches Info, Notes, and other panes |
| Daily papers | Tools menu |
| Literature discovery | Tools menu |
| Research projects | Tools menu |

Opening a PDF tab makes AI chat the full-height primary pane on the right and
starts preparing the exact PDF attachment before the first question when a model
is configured. The reader keeps its ordinary Info, Notes, and plugin panes
mounted and reachable from the sidenav. A reader item with several PDFs stays
bound to the attachment actually open on screen; a later manual collapse is
respected when returning to the tab. The Reader's translation button uses the
same Translated Only and Side by Side commands as the existing library-item
right-click menu.

Release installers are built by the repository-root
`.github/workflows/desktop-release.yml` on a `desktop-v*` tag.

### Known gaps in what is built

Stated plainly because a gap someone rediscovers is a gap that wasted their time:

- Desktop builds are **unsigned**. macOS refuses the first launch; it has to be
  allowed once under System Settings → Privacy & Security → Open Anyway.
  Control-click → Open, the instruction everyone knows, stopped working in macOS
  15 for code with no usable signature. Windows gets a portable archive, not an
  installer, because the NSIS path needs Cygwin and this project has no way to
  test it.
- Pharos does not read Zotero's own settings, so a **relocated Zotero data
  directory is not found** — it silently creates an empty `~/Zotero` instead.
  Work around it by passing `-datadir` on the first launch.
- Discovery reads search metadata and abstracts, not full papers.
- Research projects persist records supplied by the researcher. Nothing runs
  code, allocates GPUs, or validates a metric. A `verified` record is a user's
  judgement, not independent reproduction.
- Zotero cloud sync is metadata-only and one-way into Pharos.
- Tags, paper-level notes, direct arXiv-link import and one-click
  discovery-to-library are not complete end-to-end in the web client.
- Discovery sources are abstract-only until the paper is in the library. A
  source with no `paper_id` cannot carry a page number, and evidence drawn from
  it is marked `abstract_only` rather than given a plausible-looking one.
- The web client has no test runner at all — `tsc -b` and a production build are
  the only gates. A pure predicate that decided whether to show a "translation
  degraded" banner was wrong for a year because nothing could have caught it.

## Next

In rough order. Each is a workstream, not a ticket.

1. ~~**Make the Zotero foundation version-compatible.**~~ **Done.** The client is
   on Zotero 8.0.5/userdata schema 123, production shared-library mode is
   enabled, Pharos-native data stays in the sidecar, and the Zotero → Pharos →
   Vibero copied-library round trip kept all 279 attachments intact.
2. ~~**Finish the client as the daily-use product.**~~ **Done.** The reader, AI
   chat, Daily Papers, discovery, projects and the data directory are at parity
   with the web client; a final module-by-module audit found ten differences and
   all ten are closed. Eleven deliberate divergences remain, each with its reason
   recorded. See [`PHASE-PARITY-3.md`](PHASE-PARITY-3.md).
3. **Sign and notarise desktop builds.** Needs an Apple Developer account; the
   build already skips notarisation cleanly when credentials are absent, and
   `app/config.sh` documents exactly which values to set. This is the only item
   here blocked on something money buys rather than something we write.
4. **Page-addressable evidence.** Anchoring a claim to a page and a region
   rather than to a whole document. This is the foundation the next three items
   rest on. *In progress: `PaperChunk` and `Evidence` exist; the client surface
   does not.*
5. **Grounded paper Q&A** — answers that cite the passage they came from, so a
   reader can check rather than trust.
6. **Evidence-aware idea workflow** — proposing directions that carry the
   evidence they rest on.
7. **Claim-to-result bindings** — a claim that knows which result supports it,
   and notices when that result changes.
8. **Writing.** The last stage of the arc, and the one that makes "一体化" true
   rather than aspirational.

The detailed contract for 2–6 is in
[`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md).

## Not doing

Each of these was considered and decided against. Reversing one is a decision,
not a refactor.

- **Rebuilding a reference manager.** Zotero's library, reader, annotations,
  citation styles and 760+ translators took a decade to mature. The desktop
  client is built from Zotero source precisely so that none of it is
  reimplemented. Do not write a paper list, a PDF reader, or a metadata
  extractor for the client.
- **A second primary desktop library or a full Local API mirror.** The desktop
  client uses Zotero's own library. Local API, Connector and cloud import remain
  companion integrations, not a duplicate source of truth.
- **A second desktop shell.** The Tauri client was removed in favour of the
  Zotero-based one. Do not add Electron, Tauri, or another wrapper.
- **Running translation, or any Python engine, inside the client.** BabelDOC has
  native dependencies and an AGPL boundary; it stays an arm's-length subprocess
  behind the backend. The client uploads and polls.
- **Executing experiments.** Pharos records research; it does not run code or
  allocate compute. The backend says so in `automation_notice`, and the client
  carries that wording verbatim rather than paraphrasing it.
- **Write-back into Zotero Cloud.** Import is one-way and metadata-only. Two
  systems both claiming to own the same library is a data-loss shape.
- **Storing model API keys in the browser or in prefs.** Backend, encrypted, or
  the OS credential store. Nowhere else.
- **A writing environment inside the desktop client.** Reading tools belong
  beside the reader; writing belongs in the web client, which is a writing
  surface. Mixing them makes both worse.
- **Rebranding Zotero's tag palette, or anything else users have already
  applied.** Tag colours are stored as literal hex values in the database;
  changing the palette silently restyles tags someone already assigned.
