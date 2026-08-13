# Zotero integration architecture

Pharos desktop is built on Zotero and uses Zotero's local library as its own
reference-management foundation. Zotero is not an import format and the desktop
client does not maintain a second mirror of the same items.

The complete data and safety contract lives in
[`CLIENT_DATA_ARCHITECTURE.md`](CLIENT_DATA_ARCHITECTURE.md). This document
describes how the direct desktop path relates to the remaining web and cloud
integrations.

## Desktop: direct local library

Pharos opens the same Zotero data directory and uses Zotero's own APIs for:

- personal and group libraries;
- nested collections and many-to-many membership;
- every Zotero item type;
- creators, tags, related items, saved searches, and trash;
- linked and stored attachments, including local-only PDFs;
- notes, highlights, annotations, citation data, and sync state.

There is no “sync local Zotero” button in this architecture. Opening Pharos
after closing Zotero shows the same library because it is the same library.

Zotero, Vibero, and Pharos must take turns. SQLite's exclusive lock prevents
concurrent access; Pharos must turn that low-level error into a clear instruction
to close the other application.

## Pharos-native records

Pharos-only records do not belong in Zotero's schema. Today, AI conversations,
paper profiles, translation metadata, and research workflow records live in the
optional backend. Daily Papers also has a versioned, user-selected portable
Vault for backup and migration; the backend database remains its online working
copy. The desktop reserves `<Zotero data directory>/pharos-local.sqlite` for a
future local sidecar, but no feature creates or writes that file yet.

Any future sidecar record must reference a Zotero object through
`(libraryID, key)` and ship with explicit schema, migration, backup, and removal
tests. It must remain removable without changing a Zotero item or PDF.

## Browser and remote devices: Zotero Cloud

A browser cannot open `zotero.sqlite` or a local attachment directory. The web
companion may therefore use Zotero OAuth and the Zotero Web API to access the
part of a user's library that exists in Zotero Cloud.

That path has unavoidable limits:

- a local-only PDF is unavailable until the user explicitly uploads it;
- OAuth is registered once by the Pharos operator, not once per end user;
- cloud metadata does not replace the desktop's direct local view;
- Pharos does not silently upload an attachment or enable cloud write-back.

## Local API and Connector

The Zotero Local API and `zotero-connector/` remain integration tools, not the
desktop library foundation.

Possible uses include:

- a browser companion talking to a running Zotero instance;
- explicit automation from another process;
- a future authenticated notification bridge;
- compatibility for a thin client that is not built from Zotero source.

They must not be used to mirror the whole library back into the Zotero-derived
desktop client. That would add latency, lose transaction semantics, and recreate
the two-authority problem that direct sharing removes.

The current Connector transport preview still advertises data capabilities as
disabled until pairing, credential-store handoff, notifier behavior, and
transaction tests are complete.

## Stable identity

The durable identity of a Zotero entity is:

```text
(libraryID, key)
```

For cross-provider records, add the provider/source identity explicitly. DOIs
and titles are not unique identifiers, and Zotero's numeric `itemID` must not be
the sole external reference.

## Security boundaries

- Development and tests never launch against the real Zotero data directory.
- Pharos never adds private schema to `zotero.sqlite`.
- Absolute attachment paths stay inside the desktop process.
- Local attachments remain local unless the user explicitly invokes a cloud
  workflow that needs the file.
- OAuth secrets remain server-side; desktop tokens stay in the OS credential
  store.
- A schema mismatch refuses startup instead of migrating the real library.
