# Pharos Daily Vault v1

Pharos Daily Vault is the portable, user-owned representation of the **Daily
Papers** module. The server database remains the online working copy in v1; the
Vault is a complete recovery copy that can be moved through iCloud Drive,
OneDrive, Dropbox, Syncthing, an external disk, or any ordinary folder.

The design has four non-negotiable properties:

1. A different device can restore Daily Papers by opening the directory.
2. Database and account identifiers never become part of the portable format.
3. A partially completed write cannot invalidate the last complete snapshot.
4. Importing the same Vault repeatedly is idempotent.

## Directory layout

The selected directory is the Vault root. Its display name is not significant;
`pharos-vault.json` identifies it.

```text
<any directory name>/
├── pharos-vault.json
├── profiles/
│   └── <sha256>.json
└── days/
    └── YYYY/
        └── MM/
            └── DD/
                └── <sha256>.json
```

Profile and day files are content-addressed and immutable. Updating a snapshot
writes new content files first and writes `pharos-vault.json` last. The manifest
is therefore the commit marker: every path it names already exists and carries
the expected SHA-256 digest. Older unreferenced revisions may remain in the
directory; Pharos v1 never deletes unknown or historical files.

All JSON files use UTF-8, LF line endings, two-space indentation, and a final
newline. Dates use `YYYY-MM-DD`; timestamps use RFC 3339 / ISO 8601.

## Manifest

The machine-readable schema is
[`schemas/daily-vault/v1/manifest.schema.json`](../schemas/daily-vault/v1/manifest.schema.json).

```json
{
  "$schema": "https://raw.githubusercontent.com/hyyyyyyz/Pharos/main/schemas/daily-vault/v1/manifest.schema.json",
  "kind": "pharos.daily.vault",
  "format_version": 1,
  "vault_id": "91624e4d-376c-4608-97bb-f5449fd68096",
  "created_at": "2026-07-27T08:30:00.000Z",
  "updated_at": "2026-07-27T09:12:44.000Z",
  "generator": "Pharos",
  "profile": {
    "path": "profiles/730bf8d9....json",
    "sha256": "730bf8d9..."
  },
  "days": [
    {
      "date": "2026-07-27",
      "path": "days/2026/07/27/51635c07....json",
      "sha256": "51635c07...",
      "paper_count": 18
    }
  ]
}
```

`vault_id` identifies the directory, not a Pharos account. It prevents a path
that was replaced outside Pharos from being silently overwritten: if the
remembered id and the manifest id differ, automatic saving pauses until the user
chooses **restore from this directory** or **replace the directory with the
current account**.

## Profile snapshot

A profile file has `kind = "pharos.daily.profile"` and `schema_version = 1`.
It contains:

- the IANA timezone reported by the client, when available;
- followed arXiv categories;
- the daily result cap and module enabled state;
- every research direction, including disabled directions, exact keyword text,
  and display/match order.

Whitespace around a keyword is preserved. This is intentional: some match terms
use surrounding spaces as a word-boundary guard and stripping them would change
which papers enter the feed.

## Day snapshot

A day file has `kind = "pharos.daily.issue"` and `schema_version = 1`. It stores
the visible order for that account and, for each paper:

- version-free arXiv id and digest date;
- original English title, authors, abstract, categories, venue, and source URLs;
- the exporting account's matched direction and keywords;
- Chinese summary and core reading blocks when a model completed them;
- paper scores, personalized relevance/recommendation, model name, timestamps,
  and the honest `pending | done | error` reading state;
- the source sweep summary, when one exists.

This preserves what the user actually saw. Restore does not call a model to
regenerate old summaries.

## Data that is deliberately excluded

A Vault must never contain:

- Pharos user ids, email addresses, password hashes, JWTs, or sessions;
- database primary keys;
- private Library paper ids or ownership relationships;
- LLM API keys or provider secrets;
- Zotero API keys, OAuth tokens, or OAuth application credentials;
- administrator flags or deployment secrets.

The importing account owns every restored user-scoped record. The backend never
trusts ownership information from a local file.

## Restore and merge semantics

`POST /api/daily/vault/import` validates the complete archive before the request
transaction commits.

- Papers are keyed by version-free arXiv id.
- A missing paper is inserted once.
- Missing bibliographic fields may be filled on an existing paper.
- A completed archived reading may upgrade a pending/error server row.
- A completed server reading is never overwritten by an older Vault reading.
- Reader-relative relevance and recommendation are not written into the shared
  database row; they are recomputed from the restored account directions.
- A paper already stored under another digest date is not moved, because Daily
  paper rows are shared by all accounts on one Pharos instance.
- Import never restores `imported_paper_id` and never creates a Library PDF.
- Profile restore replaces only the authenticated account's Daily settings and
  directions. It cannot edit another account.
- Repeating the same import reports existing records rather than duplicating
  them.

The browser and desktop clients verify every file hash before sending an archive
to this endpoint. Pydantic validation on the backend remains authoritative and
rejects unknown fields, unsupported versions, unsafe URLs, duplicate dates,
duplicate arXiv ids, excessive counts, and oversized text.

## Client behavior

| Client | v1 behavior |
| --- | --- |
| Desktop client (macOS, Windows, Linux) | Papers are saved into the Zotero library itself rather than a separate vault directory, so no filesystem scope has to be requested or persisted. |
| Chrome / Edge desktop | File System Access API, directory handle stored in IndexedDB, permission may need to be granted again after reopening. |
| Safari / Firefox | Portable JSON export/import fallback; these browsers cannot be promised continuous writable-directory access. |
| iOS / Android | JSON fallback for now. A later adapter must use iOS security-scoped documents and Android Storage Access Framework rather than desktop paths. |

No web client can continue writing after its tab has been closed. “Automatic”
means after Daily data changes and periodically while Pharos is open and the
directory remains authorized.

## Versioning

`format_version` is the directory-format major version. A client that does not
recognize it must not write to the Vault. Every referenced record also has its
own `kind` and integer `schema_version`, allowing a future client to migrate one
record family without silently reinterpreting another.

V1 readers reject unknown fields at the backend boundary. A future incompatible
layout must use a new manifest `format_version`; it must not mutate a v1
directory in place without first preserving the v1 manifest and referenced
files.
