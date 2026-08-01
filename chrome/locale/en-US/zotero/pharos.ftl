# Pharos-specific strings. Zotero's own strings stay in zotero.ftl so that a
# future hand-merge of an upstream change does not collide with ours.

pharos-error-signed-out = Not signed in to Pharos
pharos-error-signed-out-detail = Sign in to your Pharos account in Settings to use this.

## Layout-preserving translation

pharos-translate-title = Translating with Pharos
pharos-translate-column-attachment = Attachment
pharos-translate-column-status = Status

pharos-translate-menu = Pharos Translation
pharos-translate-menu-mono = Translated Only
pharos-translate-menu-dual = Side by Side

pharos-translate-status-uploading = Uploading…
pharos-translate-status-queued = Queued…
# $stage (String) - the engine's label for the current step
# $percent (Number) - 0–100
pharos-translate-status-running = { $stage } { $percent }%
pharos-translate-status-downloading = Downloading…

# Appended to the filename of the produced attachment
pharos-translate-suffix-mono = Translated
pharos-translate-suffix-dual = Bilingual

pharos-translate-error-missing-file = The attachment's file is missing
pharos-translate-error-failed = Translation failed
pharos-translate-error-timeout = Translation timed out
pharos-translate-error-cancelled = Cancelled
pharos-translate-error-no-output = The translation produced no file in this format
pharos-translate-error-disabled = Whole-document translation is switched off for this account
