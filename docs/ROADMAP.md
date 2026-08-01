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

**Desktop client** (`client/`, built from Zotero source) — Zotero's library,
reader, annotations, citation styles and translators, plus:

| Feature | Where it lives |
| --- | --- |
| Layout-preserving translation | Right-click a PDF |
| AI chat about the open paper | Item pane section |
| Daily papers | Tools menu |
| Literature discovery | Tools menu |
| Research projects | Tools menu |

Release installers are built by `client/.github/workflows/release.yml` on a `v*`
tag.

### Known gaps in what is built

Stated plainly because a gap someone rediscovers is a gap that wasted their time:

- Desktop builds are **unsigned**. macOS needs Control-click, Open on first
  launch. Windows gets a portable archive, not an installer, because the NSIS
  path needs Cygwin and this project has no way to test it.
- Discovery reads search metadata and abstracts, not full papers.
- Research projects persist records supplied by the researcher. Nothing runs
  code, allocates GPUs, or validates a metric. A `verified` record is a user's
  judgement, not independent reproduction.
- Zotero cloud sync is metadata-only and one-way into Pharos.
- Tags, paper-level notes, direct arXiv-link import and one-click
  discovery-to-library are not complete end-to-end in the web client.
- The client's user-facing help links still point at Zotero's support pages and
  forums. They must be changed before any public release, or Pharos users will
  file our bugs in Zotero's forum.
- The internal Python package is still named `xuanzang`, from an earlier
  direction. Renaming it is mechanical but touches imports everywhere.

## Next

In rough order. Each is a workstream, not a ticket.

1. **Sign and notarise desktop builds.** Needs an Apple Developer account; the
   build already skips notarisation cleanly when credentials are absent, and
   `app/config.sh` documents exactly which values to set.
2. **Point help and support links at Pharos.** See the gap above.
3. **Page-addressable evidence.** Anchoring a claim to a page and a region
   rather than to a whole document. This is the foundation the next three items
   rest on.
4. **Grounded paper Q&A** — answers that cite the passage they came from, so a
   reader can check rather than trust.
5. **Evidence-aware idea workflow** — proposing directions that carry the
   evidence they rest on.
6. **Claim-to-result bindings** — a claim that knows which result supports it,
   and notices when that result changes.
7. **Writing.** The last stage of the arc, and the one that makes "一体化" true
   rather than aspirational.

The detailed contract for 3–7 is in
[`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md).

## Not doing

Each of these was considered and decided against. Reversing one is a decision,
not a refactor.

- **Rebuilding a reference manager.** Zotero's library, reader, annotations,
  citation styles and 760+ translators took a decade to mature. The desktop
  client is built from Zotero source precisely so that none of it is
  reimplemented. Do not write a paper list, a PDF reader, or a metadata
  extractor for the client.
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
