# Zotero integration architecture

Pharos treats Zotero as a first-class local data source, not as an import
format. Zotero remains authoritative for bibliographic metadata and local
attachments. Pharos stores a local mirror for fast navigation and offline
display, while Pharos-native research projects, ideas, experiments, and AI
conversations remain separate objects linked back to Zotero entities.

## Providers

All providers implement one entity model and one capability contract:

| Provider | Desktop data | Local files | Realtime | Write-back |
| --- | --- | --- | --- | --- |
| Pharos Connector (transport preview) | Negotiated, disabled | Disabled | Disabled | Disabled |
| Zotero Local API | Complete read projection | Yes | Polling | No |
| Zotero Cloud | Synced cloud data | Only uploaded files | Remote polling | Scoped API writes |

Provider priority on desktop will be Connector, Local API, then Cloud once the
Connector advertises tested data capabilities. Today the Connector truthfully
advertises all data capabilities as disabled, so desktop uses the complete
Local API projection. Web and mobile clients use Cloud. A cloud link never
makes a local-only PDF remotely available and Pharos never uploads a Zotero
attachment without an explicit user action.

## Stable identity

The durable identity of a Zotero entity is:

```text
(source_id, library_id, object_key)
```

DOIs and titles are not identifiers. Zotero's numeric internal item IDs are
also not stable across profiles. Attachments exposed to the WebView receive an
opaque `public_id`; absolute paths stay inside Rust.

## Local mirror

The desktop client owns a versioned SQLite mirror in its application data
directory. It stores:

- libraries and group libraries;
- nested collections and many-to-many item membership;
- every Zotero item type, including notes and annotations;
- tags, creators, relations, saved searches, trash state;
- attachment metadata and private local path locators;
- full-text index versions, with full content fetched on demand;
- sync cursors and deletion tombstones.

Raw Zotero JSON is retained alongside projections so new Zotero fields do not
disappear when an older Pharos client mirrors them. PDF bytes are never copied
into SQLite.

## Local API phase

The Local API provider talks only to `127.0.0.1:23119`. It uses API version 3,
fetches the complete object graph, and advances `since` cursors only after a
successful SQLite transaction. When Zotero is closed, the last mirror remains
available. The UI calls this operation “连接本机 Zotero”, not “导入”.

## Connector phase

`pharos-connector@selab.top` is a Zotero 7/8 bootstrapped extension. Version
`0.1.0` establishes the hardened localhost transport and capability handshake;
it does not yet expose data or writes. Future releases will use Zotero's
JavaScript API and `Zotero.Notifier` for realtime changes and safe writes. Its
protected endpoints require a paired bearer token; the pairing UI and
operating-system credential-store handoff must land before a data capability is
enabled. The Connector, never Pharos, will perform Zotero transactions.

## Security boundaries

- The WebView cannot choose a host, Local API URL, or filesystem path.
- Localhost Connector requests require authentication; tokens never appear in
  query strings or logs.
- The custom attachment protocol accepts opaque IDs and supports byte ranges.
- Direct writes to `zotero.sqlite` are forbidden.
- Local attachments remain local unless the user explicitly imports or uploads
  a file for a Pharos workflow.
