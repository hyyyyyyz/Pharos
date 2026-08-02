# Client foundation audit — 2026-08-02

This checkpoint records the handoff from the earlier isolated-library design to
the current Zotero-derived client direction. It exists so implementation work
can be judged against evidence rather than against whichever handoff prompt was
read most recently.

## What was learned from Vibero

Vibero does not achieve local interoperability through a mirror, a connector,
or Zotero Cloud. Its installed application has its own application profile and
visible name, while its preferences point to the same Zotero data directory as
Zotero. The shared objects therefore remain normal Zotero objects.

Vibero keeps feature-specific state in a separate `vibeDB.sqlite`, linked to
Zotero objects by stable library/key identities. This is the useful architectural
pattern for Pharos; Vibero's other product features are not a template for the
Pharos interface or workflow.

## Claude work worth preserving

The recent Pharos feature work is not discarded. In particular:

- AI Chat restores per-paper history and avoids stale asynchronous history when
  the selected item changes quickly.
- Translation imports both mono and bilingual outputs, keeps source/translation
  relations in both directions, and preserves collection membership.
- The Pharos module rail is resizable, keyboard accessible, persisted, and
  clamped to usable limits.
- Daily Papers is a complete three-pane desktop workflow with dates, filtering,
  job status, detail, AI reading, and native Zotero import.
- The development launcher isolates its data directory and is still the correct
  way to run untrusted builds.

These are product-layer changes that should be replayed on the compatible Zotero
core rather than rewritten without cause.

## What needs correction

The following assumptions do not match the current product direction:

- a permanent `~/Pharos/pharos.sqlite` reference library;
- treating local Zotero as an external provider to mirror into that library;
- a desktop Local API/Connector pipeline as the primary access path;
- describing the FastAPI database as the authority for local desktop items;
- giving the web client equal implementation priority before the desktop client
  is safe and comfortable for daily use.

OAuth, Local API, and the Connector can still serve web, remote-device, or
explicit integration scenarios. They are not the desktop client's library
foundation.

## Blocking compatibility finding

| Application | Core version | Zotero userdata schema |
| --- | --- | --- |
| Installed Zotero | 8.0.5 | 123 |
| Installed Vibero | 8.0 | 123 |
| Current Pharos source | 10.0.SOURCE | 129 |

Pharos migrations 124-129 add `lastRead`, group-admin state, normalized search
columns, revised saved-search storage, attachment-path cleanup, and client
versions. The current core reads and writes several of those fields. It is not
safe either to skip the migrations or to run them on the real Zotero 8 library.

## Chosen correction

Re-establish the desktop core on the official Zotero 8.0.5 baseline, then replay
the Pharos branding and product commits in small, testable groups. Preserve the
independent Pharos profile and credentials, share the Zotero library only after
the compatibility suite passes, and introduce a Pharos sidecar for local-only
state.

The implementation checkpoints are:

1. Documentation and safety boundary.
2. Zotero 8.0.5 clean baseline and upstream test result.
3. Branding/profile/build changes.
4. Shared-library identity and lock diagnostics.
5. Pharos API/auth/theme/rail.
6. Translation and AI Chat.
7. Daily Papers, discovery, and projects.
8. Copied-library Zotero ↔ Pharos ↔ Vibero round trip.
9. Release build and user-data migration guidance.

