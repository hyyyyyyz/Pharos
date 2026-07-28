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
| Pharos Connector | Complete | Yes | Yes | Yes |
| Zotero Local API | Complete read projection | Yes | Polling | No |
| Zotero Cloud | Synced cloud data | Only uploaded files | Remote polling | Scoped API writes |

Provider priority on desktop is Connector, Local API, then Cloud. Web and
mobile clients use Cloud. A cloud link never makes a local-only PDF remotely
available and Pharos never uploads a Zotero attachment without an explicit
user action.

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

`pharos-connector@selab.top` is a Zotero 7/8 bootstrapped extension. It uses
Zotero's JavaScript API and `Zotero.Notifier` to provide realtime changes and
safe writes. Its localhost endpoints require a paired bearer token stored in
the operating-system credential store on the Pharos side. The Connector, never
Pharos, performs Zotero transactions.

## Security boundaries

- The WebView cannot choose a host, Local API URL, or filesystem path.
- Localhost Connector requests require authentication; tokens never appear in
  query strings or logs.
- The custom attachment protocol accepts opaque IDs and supports byte ranges.
- Direct writes to `zotero.sqlite` are forbidden.
- Local attachments remain local unless the user explicitly imports or uploads
  a file for a Pharos workflow.

