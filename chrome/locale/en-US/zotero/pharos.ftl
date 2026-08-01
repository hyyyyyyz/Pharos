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

## Preferences

pharos-prefs-pane = Pharos

pharos-prefs-account-header = Pharos Account
pharos-prefs-account-intro = Sign in to translate documents, chat about a paper, and receive the daily arXiv digest.
pharos-prefs-email = Email
pharos-prefs-password = Password
pharos-prefs-sign-in = Sign In
pharos-prefs-signing-in = Signing in…
pharos-prefs-error-incomplete = Enter your email and password.
pharos-prefs-error-sign-in = Could not sign in.

pharos-prefs-signed-in-as = Signed in as
pharos-prefs-sign-out = Sign Out
pharos-prefs-sign-out-all = Sign Out Everywhere
pharos-prefs-sign-out-all-confirm = This signs out every device where you are signed in to Pharos. Continue?
pharos-prefs-sign-out-all-help = Signing out everywhere revokes every token this account has ever been issued.

pharos-prefs-server-header = Server
pharos-prefs-server-help = Pharos is open source and can be self-hosted. Point this at your own instance to use it instead. Changing it signs you out, since a token only works on the server that issued it.
pharos-prefs-server-url = Address

## Chat

section-pharos-chat =
    .label = Ask Pharos
pharos-chat-placeholder =
    .placeholder = Ask about this paper…
pharos-chat-send = Send

pharos-chat-status-connecting = Connecting…
pharos-chat-status-preparing = Reading the paper…
pharos-chat-status-thinking = Thinking…

pharos-chat-error-prepare = Could not read this paper.
pharos-chat-error-prepare-timeout = Reading this paper took too long.
pharos-chat-error-failed = The answer could not be generated.
pharos-chat-error-empty = No answer came back.

pharos-error-unreachable = Could not reach the Pharos server.

## Daily arXiv digest

pharos-daily-menu = Daily Papers…
pharos-daily-window =
    .title = Daily Papers
pharos-daily-refresh = Fetch Now
pharos-daily-refreshing = Sweeping arXiv…
pharos-daily-loading = Loading…
pharos-daily-empty = Nothing for this day yet.
pharos-daily-error = Could not load the digest.
# $count (Number) - papers matched for the selected day
pharos-daily-count = { $count } papers
pharos-daily-unread = Not read yet
pharos-daily-read-failed = Reading failed
pharos-daily-save = Save to Library
pharos-daily-saving = Saving…
pharos-daily-saved = Saved
pharos-daily-save-failed = Could not save this paper.
pharos-daily-open = Open on arXiv
pharos-daily-matched = Matched

pharos-daily-highlight-contribution = Contribution
pharos-daily-highlight-innovation = Novelty
pharos-daily-highlight-method = Method
pharos-daily-highlight-results = Results

## Literature discovery

pharos-discovery-menu = Find Literature…
pharos-discovery-window =
    .title = Find Literature
pharos-discovery-placeholder =
    .placeholder = Search arXiv and OpenAlex…
pharos-discovery-search = Search
pharos-discovery-searching = Searching…
pharos-discovery-hint = Search arXiv and OpenAlex, and Pharos will read what it finds.
pharos-discovery-empty = Nothing matched.
pharos-discovery-error = The search failed.
# $count (Number) - results returned
pharos-discovery-count = { $count } results
# $count (Number) - times the paper has been cited
pharos-discovery-citations = { $count } citations
pharos-discovery-analyze = Read It
pharos-discovery-analyzing = Reading…
pharos-discovery-open = Open

pharos-discovery-section-contribution = Contribution
pharos-discovery-section-core-trick = Key idea
pharos-discovery-section-method = Method
pharos-discovery-section-results = Results
pharos-discovery-section-limitations = Limitations
pharos-discovery-rules-note = Summarised without a model. Use "Read It" for a proper reading.
