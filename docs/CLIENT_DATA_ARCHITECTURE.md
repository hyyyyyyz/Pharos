# Desktop data architecture

This document is the current source of truth for how the Pharos desktop client
coexists with Zotero and other Zotero-derived applications such as Vibero.

## Product rule

Pharos is a Zotero-derived research client, not a second reference manager.
The desktop client must use the user's Zotero library directly so that the same
items, collections, attachments, PDFs, notes, and annotations are available when
the user opens Zotero, Vibero, or Pharos.

Sharing the library does **not** mean sharing the whole application identity.

| Concern | Shared with Zotero | Pharos-owned |
| --- | --- | --- |
| Bibliographic items and collections | Yes | No duplicate mirror |
| Attachments, `storage/`, PDFs, notes, annotations | Yes | No duplicate mirror |
| Zotero sync state and citation data | Yes | No private columns or tables |
| Application profile, caches, window state | No | Separate Pharos profile |
| Bundle ID, branding, URL protocol | No | Pharos identity and `pharos://` |
| Pharos login token and provider settings | No | OS credential store / Pharos profile |
| AI conversations, Daily Papers state, research workflow state | No | Pharos sidecar and/or Pharos backend |

## Current safety status

Shared-library mode is enabled in production and guarded.

- Pharos is aligned to the Zotero 8.0.5 baseline and userdata schema `123`;
- `DATA_DIR_NAME: "Zotero"` and `DB_NAME: "zotero"` make a release open the
  same library and attachments while `ID: "pharos"` keeps the application
  profile, URL protocol and branding separate;
- before any integrity check, backup or migration can touch the database,
  `Zotero.Pharos.SharedLibrary.assertMigrationAllowed()` requires the on-disk
  schema to match exactly;
- the Zotero → Pharos → Vibero copied-library round trip retained all 279 test
  attachments and left the schema unchanged.

A future baseline change reopens this gate. Pharos must never migrate a user's
shared Zotero library merely because its own source moved ahead: mismatch means
refuse to open, then ship a compatible client. Development and CI remain
isolated even though production intentionally shares the library.

The remaining data-directory problem is discovery rather than schema safety:
a fresh Pharos profile knows the default `~/Zotero`, but older releases did not
read an official Zotero profile that relocated its library. The client must
adopt that sibling profile setting only after the configured directory is
proved to contain the expected `zotero.sqlite`; command-line and explicit
Pharos preferences keep precedence.

## Shared Zotero data plane

After compatibility work is complete, a normal Pharos release uses the same
Zotero data directory selected by Zotero. That includes:

```text
<Zotero data directory>/
├── zotero.sqlite
├── storage/
├── styles/
├── translators/
└── ... Zotero-managed files
```

Pharos reads and writes these objects through Zotero's own data model and
transaction APIs. It must not:

- mirror the library through the Local API for its primary desktop view;
- add Pharos-only tables or columns to `zotero.sqlite`;
- parse or mutate the live SQLite database from an external process;
- copy a local-only PDF to the cloud without an explicit user action.

The stable identity used by Pharos extensions is `(libraryID, key)`. For an
attachment-specific record, use the attachment's own key. Numeric `itemID`
values are process-local cache identifiers and must not be the only durable
link stored outside the Zotero database.

## Exclusive access

Zotero uses an exclusive SQLite locking mode. Zotero, Vibero, and Pharos may
take turns using one library, but they must not use it simultaneously.

The second application must fail safely and explain which action is required:

> This Zotero library is already open. Close Zotero, Vibero, or another Pharos
> window that is using it, then try again.

There is no automatic lock breaking, process killing, or database copying.

## Pharos sidecar

Pharos-native local data belongs in a separate, versioned sidecar. The intended
layout is:

```text
<Zotero data directory>/
└── pharos/
    ├── pharos.sqlite
    ├── indexes/
    ├── cache/
    └── logs/
```

The sidecar may contain AI conversation metadata, paper profiles, Daily Papers
state, embeddings, research-workflow records, and task caches. Deleting it may
remove Pharos-only history, but must never damage Zotero items, attachments, or
annotations. Large user-owned source files remain Zotero attachments unless a
feature explicitly creates a Pharos artifact.

Records that also exist on the Pharos backend need an explicit sync state and a
stable local identity. The backend is an optional service plane for model calls,
translation, cross-device continuity, and web access; it is not the authority
for the desktop Zotero library.

## Application identity

The following remain independent even though the library is shared:

- Gecko application profile;
- bundle/application identifier;
- Pharos icon and visible name;
- `pharos://` protocol;
- OS credential-store entries;
- update channel;
- caches, preferences, and window layout.

Do not make Pharos pretend to be Zotero merely to obtain the same data directory.
Data-directory and database names must be configured separately from the visible
application identity.

## Development safety

Development, tests, and CI never use the real Zotero data directory.

- `client/app/scripts/run_pharos_dev` continues to force an isolated data
  directory and refuses `~/Zotero`.
- Automated compatibility tests operate on a copied fixture library.
- A test may inspect the real library only through explicitly read-only tooling;
  it may not launch a development client against it.
- Database migration experiments always start from a disposable copy and retain
  the original checksum.

This is compatible with the product direction: release builds share the user's
library after passing compatibility gates; development builds remain isolated.

## Compatibility and release gates

Pharos may enable shared-library mode only after all of the following pass on a
copy of a real library:

1. The Pharos core bundles the same Zotero userdata compatibility level as the
   supported Zotero release.
2. Zotero creates an item, nested collection, PDF, note, and annotation; Pharos
   opens and edits them after Zotero exits.
3. Zotero reopens the Pharos-edited library without migration, warning, or data
   loss.
4. Vibero completes the same round trip.
5. `PRAGMA integrity_check` returns `ok` and `PRAGMA foreign_key_check` returns
   no rows.
6. The `userdata` schema version remains unchanged throughout the round trip.
7. A simultaneous launch fails with a clear lock message and leaves all files
   untouched.
8. Default and custom Zotero data directories both work.
9. Removing the Pharos sidecar leaves the Zotero library fully usable.

The supported Zotero version and schema level must be recorded in
`client/UPSTREAM.txt` and tested on every desktop release.

## Web and cloud role

The browser cannot directly open a local SQLite database or local-only Zotero
attachments. Zotero OAuth and the Zotero Web API therefore remain useful for
the web companion and remote devices. That cloud path is separate from the
desktop architecture and does not turn a local-only PDF into a cloud file.

