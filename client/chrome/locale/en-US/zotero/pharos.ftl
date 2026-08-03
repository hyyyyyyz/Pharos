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
# The display name. The rail's account row prefers it and moves the address to
# the tooltip -- the address identifies the account, so it never disappears.
pharos-prefs-display-name = Display name
pharos-prefs-display-name-input =
    .placeholder = Blank shows your email
pharos-prefs-display-name-save = Save
pharos-prefs-display-name-saved = Saved. The rail shows this name now.
pharos-prefs-display-name-cleared = Cleared. The rail shows your email again.
pharos-prefs-display-name-failed = Could not save. Please try again.
# An account-level switch, not a device preference -- changing it in the web app
# changes it here too.
pharos-prefs-pdf-translation = Whole-PDF translation
pharos-prefs-pdf-translation-help = Rebuilds the whole paper with its layout intact. It is slow and spends API budget. Off hides the translate actions, the status column and the reading modes.
pharos-prefs-pdf-translation-failed = Could not save. Please try again.
pharos-prefs-sign-out = Sign Out
pharos-prefs-sign-out-all = Sign Out Everywhere
pharos-prefs-sign-out-all-confirm = This signs out every device where you are signed in to Pharos. Continue?
pharos-prefs-sign-out-all-help = Signing out everywhere revokes every token this account has ever been issued.

pharos-prefs-server-header = Server
pharos-prefs-server-help = Pharos is open source and can be self-hosted. Point this at your own instance to use it instead. Changing it signs you out, since a token only works on the server that issued it.
pharos-prefs-server-url = Address

## AI Chat

# The web client calls this "AI Chat" everywhere. One feature under two names is
# the one confusion a user cannot resolve on their own, so the desktop's "Ask
# Pharos" gives way to the web client's name.
section-pharos-chat =
    .label = AI Chat
# The sidenav button's tooltip, and the value collapsible-section formats into
# its aria names. Upstream builds both ids from data-pane (sidenav-${pane} /
# pane-${pane}); a missing one does not throw, it just leaves the button
# unlabelled, since DOM localization only rejects.
pane-pharos-chat = AI Chat
sidenav-pharos-chat =
    .tooltiptext = AI Chat
pharos-chat-placeholder =
    .placeholder = Ask about this paper…
pharos-chat-hint = Enter to send · Shift+Enter for a new line
pharos-chat-send = Send
pharos-chat-stop = Stop
pharos-chat-dismiss = Dismiss

## AI Chat · Conversations

section-button-pharos-chat-new =
    .tooltiptext = New Conversation
section-button-pharos-chat-more =
    .tooltiptext = Conversation Actions
pharos-chat-session-select =
    .aria-label = Choose a conversation
# The backend renames a conversation after its first question; one that has
# never been used keeps this default.
pharos-chat-untitled = Paper conversation
pharos-chat-delete = Delete This Conversation
# This promise was checked against the backend: delete_conversation() removes
# only the conversation. The paper's extracted text and the model's
# understanding of it live in their own table keyed by (user, paper) --
# PaperAiContext -- and survive, so the next question still costs no upload and
# no re-reading. The turns in the conversation do go, which the web client's
# wording leaves out.
pharos-chat-delete-confirm = Delete this AI conversation? Its messages go with it. The paper's index is kept, so the next question needs no re-upload.
pharos-chat-delete-go = Delete
pharos-chat-delete-cancel = Cancel

## AI Chat · Paper state

# The resting line under the section title. It reports what is known and never
# causes it: resolving a paper uploads the whole file, and that is paid for only
# when a question is actually asked.
pharos-chat-phase-understanding = Building an understanding of this paper
pharos-chat-phase-ready = This paper is understood
pharos-chat-phase-indexed = The paper's text has been read
pharos-chat-phase-indexed-no-model = Text read · waiting for a model
pharos-chat-phase-error = Could not build an understanding
# What to say about a paper this session has not resolved. The web client says
# "preparing paper context" here, but nothing is being prepared on the desktop
# at this point, and copying it would describe work that deliberately is not
# happening.
pharos-chat-phase-lazy = Reads this paper on your first question
# $count (Number) - characters the backend extracted
pharos-chat-phase-chars = { $count } characters read

## AI Chat · Empty state

pharos-chat-empty-ready = I have already read this paper
pharos-chat-empty-understanding = Building context for this paper
pharos-chat-empty-idle = Start a conversation about this paper
# Four openings, matching the web client. The label on the button is the
# question that gets sent.
pharos-chat-starter-contribution = What is the core contribution?
pharos-chat-starter-trick = What is the trick that actually matters?
pharos-chat-starter-evidence = How do the experiments show the method works?
pharos-chat-starter-limitations = What are this paper's limitations?

## AI Chat · When a question cannot be asked

pharos-chat-signed-out-title = Sign in to ask about this paper
pharos-chat-signed-out-detail = The Pharos backend reads the paper and calls the model, so this needs an account. Your library, reader and annotations are unaffected.
pharos-chat-signed-out-action = Sign In

pharos-chat-no-model-title = Connect your model
# There is deliberately no field to type a key into: the key exists only in the
# backend, encrypted, and never passes through this computer. What this client
# can do is say so and point at the read-only view of the model in Settings.
pharos-chat-no-model-detail = Any OpenAI-compatible endpoint works. The API key can only be entered in the Pharos web app, where the server stores it encrypted — it is never written to this computer, which is why there is no field for it here. Settings shows which model is in use.
pharos-chat-no-model-action = Show Model Settings

## AI Chat · Progress and failures

pharos-chat-status-connecting = Connecting…
pharos-chat-status-preparing = Reading the paper…
pharos-chat-status-thinking = Thinking…
# Not shown as an error: whoever pressed Stop knows why it stopped. The backend
# saves no partial answer when a stream is abandoned (stream_chat_events, the
# GeneratorExit branch), so the partial text goes too -- leaving it would put a
# turn on screen that the model does not have.
pharos-chat-stopped = Stopped. That answer was not saved.

pharos-chat-error-prepare = Could not read this paper.
pharos-chat-error-prepare-timeout = Reading this paper took too long.
pharos-chat-error-failed = The answer could not be generated.
pharos-chat-error-empty = No answer came back.
pharos-chat-error-history = Could not load this conversation.
pharos-chat-error-new = Could not start a new conversation.
pharos-chat-error-delete = Could not delete this conversation.

pharos-error-unreachable = Could not reach the Pharos server.

## Daily arXiv digest

# Vocabulary: the thing a model produces is an "AI reading", and producing one
# is "Read with AI". Continuous with the backend's own read_status / read_error,
# and it keeps what a person does distinct from what a model did.

pharos-daily-menu = Daily Papers…
pharos-daily-window =
    .title = Daily Papers
# pharos-daily-window has an attribute and no value, so Zotero.getString()
# cannot read it and throws outright in en-US. Name the module with this.
pharos-daily-heading = Daily Papers
pharos-daily-error = Could not load the digest.
pharos-daily-loading = Loading…
# $count (Number) - papers matched for the selected day
# The day's own total while a direction chip narrows the list, so a filtered
# count cannot be read as the day being thin.
pharos-daily-count-all = { $count } that day
pharos-daily-count = { $count } papers
pharos-daily-matched = Matched
# The provenance line written into the Zotero note. That note lives in the
# library permanently and looks exactly like the reader's own notes, so six
# months later it would be quoted as their own understanding. The model saw only
# the abstract (backend/pharos/daily/reader.py: "The model reads this and nothing
# else"), which means the Method and Results bullets are inferred from an
# abstract rather than read from the paper.
pharos-daily-note-provenance = Generated by { $model } from the English abstract; the model did not read the full text.
pharos-daily-note-provenance-unknown = Generated by a language model from the English abstract; the model did not read the full text.


pharos-daily-highlight-contribution = Contribution
pharos-daily-highlight-innovation = Novelty
pharos-daily-highlight-method = Method
pharos-daily-highlight-results = Results

## Daily papers · date rail

pharos-daily-rail-head = Dates
pharos-daily-rail-unreachable = No connection
pharos-daily-rail-no-directions = No directions
pharos-daily-rail-no-match = No matching dates
pharos-daily-rail-empty = Nothing yet
# $count (Number) - papers on that date with no reading yet
pharos-daily-date-pending = { $count } awaiting AI

## Daily papers · toolbar

pharos-daily-refresh = Update
pharos-daily-refreshing = Updating
pharos-daily-refresh-tooltip = Fetch today's arXiv and read it
pharos-daily-filter-all = All
pharos-daily-sort-score = Score
pharos-daily-sort-time = Date

## Daily papers · sweep progress and run failures

# $total (Number) - papers this account has matched so far
# $read (Number) - how many of them have been read
# Both come from getStatus().today, or the matching getDates() row. NOT from
# last_run: those three columns are written once at _finish_run and read zero
# for the whole time a sweep is worth watching.
pharos-daily-sweep-progress = Updating · { $total } matched · { $read } read
# $failed (Number) - readings that failed
# Appended to the line above only when the failed count is above zero. The
# leading " · " belongs to this value, and Fluent strips whitespace after "=",
# hence the string literal.
pharos-daily-sweep-failed = { " " }· { $failed } failed
pharos-daily-last-run-failed = The last update failed
# $error (String) - the reason the server recorded
pharos-daily-last-run-failed-detail = The last update failed: { $error }
# $error (String)
pharos-daily-refresh-failed = Update failed: { $error }
# Shown for a 409 in place of the server's own English prose
pharos-daily-refresh-busy = An update is already running.

## Daily papers · no reading model

# Informational, never an error: fetching genuinely works without a key.
pharos-daily-no-llm = No LLM API key is configured. Papers are still fetched, filtered and ranked by your directions, but no Chinese reading or scores can be produced. They stay "Awaiting AI", and can be read at any time once a key is set.
pharos-daily-no-llm-tooltip = No LLM API key is configured
# Shown for a 503 from the read endpoint
pharos-daily-read-unavailable = No reading model is configured, so nothing can be read.
# $name (String) - the provider
# $model (String) - the model
pharos-daily-provider = Reading model: { $name } · { $model }
pharos-daily-provider-none = No reading model configured

## Daily papers · empty states

pharos-daily-no-directions-title = No research directions yet
pharos-daily-no-directions-desc = Daily Papers filters arXiv by the research directions you define. No direction is enabled right now, so nothing can reach this view. Add one with a few keywords in Settings to start.
pharos-daily-disabled-title = Daily Papers is switched off
pharos-daily-disabled-desc = This account has Daily Papers switched off. The sweep still runs, but nothing reaches this view. Turn it back on under Settings → Daily Papers.
pharos-daily-firstuse-title = Daily Papers
pharos-daily-firstuse-desc = Every day this scans arXiv, keeps the papers matching the research directions you defined in Settings, and produces a Chinese reading, highlights and scores for each one. Press Update to fetch the first digest.
pharos-daily-nomatch-title = No papers matched your directions
pharos-daily-nomatch-desc = The sweep has run, but not one paper hit your direction keywords. Loosen a keyword, add a direction or add a category in Settings; the change re-filters immediately, with nothing fetched again.
pharos-daily-directions-label = Your directions
pharos-daily-open-settings = Open Settings
pharos-daily-edit-directions = Edit Directions
pharos-daily-refetch = Fetch Again
pharos-daily-day-unswept = This day has not been fetched
pharos-daily-day-unswept-hint = Weekends and announcement gaps usually have nothing
pharos-daily-day-nomatch = No paper matched your directions on this day
# $fetched (Number) - Day.run.fetched, the finished sweep's real total
pharos-daily-day-nomatch-hint = { $fetched } papers were fetched that day; none hit your keywords
# Paired with pharos-error-unreachable
pharos-daily-unreachable-hint = Make sure the Pharos service is running, then try again.
pharos-daily-detail-empty = Select a paper to see its reading

## Daily papers · cards and reading

pharos-daily-pending = Awaiting AI
pharos-daily-read = Read with AI
pharos-daily-reading = Reading…
pharos-daily-retry = Retry
pharos-daily-retrying = Retrying…
# The paper's own status
pharos-daily-read-failed = Reading failed
# $error (String) - the read_error a previous attempt left behind. Shown
# alongside the line above: one states the status, the other states why.
# Do not collapse them.
pharos-daily-read-failed-detail = Reading failed: { $error }
# $error (String) - this retry's own failure, on a third line
pharos-daily-retry-failed = Retry failed: { $error }
pharos-daily-score-tooltip = Overall score · includes your direction relevance
pharos-daily-open = arXiv abstract page
pharos-daily-open-pdf = PDF
# Stands in for an empty value in the info grid
pharos-daily-none = —

## Daily papers · importing

# On the desktop, importing means the local Zotero library. It does not call
# the backend's /import endpoint.
pharos-daily-import = Import to Library
pharos-daily-importing = Importing…
pharos-daily-imported = In Library
# $error (String)
pharos-daily-import-failed = Import failed: { $error }

# Research Projects still uses the four below, for "Save to Library" on every
# row of the project literature. Deleting them makes that window throw outright
# in en-US and render the bare id in zh-CN. Remove once it moves to
# pharos-daily-import* too.
# Find Literature has its own pharos-discovery-save* and no longer borrows here.
pharos-daily-save = Save to Library
pharos-daily-saving = Saving…
pharos-daily-saved = Saved
pharos-daily-save-failed = Could not save this paper.

## Daily papers · detail panel

pharos-daily-section-summary = Summary (Chinese)
pharos-daily-section-highlights = Highlights
pharos-daily-section-scores = Scores
pharos-daily-section-info = Details
pharos-daily-section-abstract = Abstract (English)

# Fixed order: relevance, recency, popularity, quality, recommendation. Overall
# is last, because it is the weighted conclusion.
pharos-daily-score-relevance = Relevance
pharos-daily-score-recency = Recency
pharos-daily-score-popularity = Attention
pharos-daily-score-quality = Quality
pharos-daily-score-recommendation = Overall
pharos-daily-score-relevance-hint = How closely it matches your research directions, computed from your keywords
pharos-daily-score-recency-hint = How recent the paper itself is
pharos-daily-score-popularity-hint = How much attention the paper itself is getting
pharos-daily-score-quality-hint = The quality of the paper itself
pharos-daily-score-recommendation-hint = A weighted overall call that includes your own relevance, so it differs per reader
pharos-daily-score-note = Relevance and Overall are computed from your research directions

pharos-daily-info-authors = Authors
pharos-daily-info-direction = Direction
pharos-daily-info-direction-hint = Which of your research directions it matched
pharos-daily-info-categories = Categories
pharos-daily-info-keywords = Matched
pharos-daily-info-keywords-hint = Which of your keywords hit this paper

## Daily papers · the data folder

# A Daily Vault is the portable copy of the digest: an ordinary folder holding
# pharos-vault.json and a set of content-addressed snapshot files. The format is
# docs/DAILY_VAULT_FORMAT.md, byte for byte the same one the web client writes,
# so a folder written by either opens in the other.
#
# The line that must not slip: this backs up the DIGEST, not the library. An
# imported paper, its PDF and its provenance note already live in the Zotero
# data directory and are covered by whatever backs that up. Any wording that
# lets someone read this as a library backup becomes the worst kind of
# misinformation on the day a disk fails -- so the three scope sentences below
# are not to be trimmed for brevity.

pharos-daily-vault = Data folder
pharos-daily-vault-saving = Saving
pharos-daily-vault-attention = Needs a decision
pharos-daily-vault-eyebrow = PHAROS DAILY VAULT · V1
pharos-daily-vault-title = Daily Papers data folder
pharos-daily-vault-close = Close
pharos-daily-vault-none = No folder connected
pharos-daily-vault-picker = Choose a folder for your Daily Papers data

## Daily papers · the data folder · status line

pharos-daily-vault-idle = No data folder chosen yet
# Shared by all four paused states; which one it is, is spelled out in the
# notice below it.
pharos-daily-vault-paused = Automatic saving is paused, waiting for you
# $days (Number) - days in the folder
# $papers (Number) - papers in the folder
pharos-daily-vault-connected = Connected · { $days } days · { $papers } papers
pharos-daily-vault-created = Folder initialised · { $days } days · { $papers } papers
pharos-daily-vault-saved = Saved · { $days } days · { $papers } papers
pharos-daily-vault-unsaved = The last save did not go through
# $added, $updated, $unchanged (Number) - the backend's merge result
pharos-daily-vault-restored = Restored · { $added } added · { $updated } updated · { $unchanged } unchanged; your research directions and filter settings were replaced with the folder's
pharos-daily-vault-disconnected = Disconnected. Nothing in the folder was deleted.
# $error (String)
pharos-daily-vault-failed = Data folder operation failed: { $error }

## Daily papers · the data folder · needs a decision

pharos-daily-vault-warn-head = Needs a decision
# $path (String) - the remembered folder
pharos-daily-vault-warn-missing = This folder is not there: { $path }. Usually an external disk that is not mounted, or a folder that was moved or renamed. Nothing is written until it comes back, and no empty folder is created in its place -- that would hide the real backup the next time it mounts.
pharos-daily-vault-warn-empty = The folder is there, but it holds no pharos-vault.json. Save now writes a fresh one -- or you may have picked the wrong folder.
pharos-daily-vault-warn-changed = The data in this folder is not what Pharos last wrote there. Another machine may have synced over it, or the path may now point somewhere else. Choose a direction first: restore it into this account, or overwrite it with this account.
# $error (String)
pharos-daily-vault-warn-broken = The folder's manifest could not be read: { $error }. It will not be overwritten until this is understood.
# $days (Number), $papers (Number)
pharos-daily-vault-warn-existing = This folder already holds a snapshot ({ $days } days, { $papers } papers). On a new machine the account is usually empty and the folder is not, so nothing is written yet: choose restore or overwrite.

## Daily papers · the data folder · actions

pharos-daily-vault-choose = Choose a folder
pharos-daily-vault-change = Change folder
pharos-daily-vault-choose-hint = Any ordinary folder. Put it in iCloud, OneDrive, Dropbox, Syncthing or on an external disk to carry it between machines.
pharos-daily-vault-save-now = Save now
pharos-daily-vault-save-now-hint = Write this account's complete Daily Papers snapshot into the folder.
pharos-daily-vault-restore = Restore from this folder
pharos-daily-vault-restore-hint = Verifies every file, merges the papers, and replaces your research directions and filter settings with the folder's.
pharos-daily-vault-overwrite = Overwrite with this account
pharos-daily-vault-overwrite-hint = Keeps the folder's own identity and writes a new snapshot from this account's data.
pharos-daily-vault-disconnect = Disconnect (nothing in the folder is deleted)

## Daily papers · the data folder · confirmations

pharos-daily-vault-restore-title = Restore from the data folder
# $days (Number), $papers (Number)
# Two sentences with a fixed division of labour: the first says what is gained,
# the second what is lost. Merged into one, the "replaces your settings" half is
# reliably the half nobody reads.
pharos-daily-vault-restore-body =
    This restores { $days } days and { $papers } papers. Papers are merged; nothing already on the server is deleted.
    It also REPLACES this account's research directions and filter settings with the folder's: your current directions, keywords, categories and daily cap are overwritten, and that cannot be undone.
pharos-daily-vault-restore-ok = Restore and replace settings
pharos-daily-vault-overwrite-title = Overwrite the folder with this account
# $days (Number), $papers (Number)
pharos-daily-vault-overwrite-body =
    The folder currently holds { $days } days and { $papers } papers. After this, Pharos keeps writing this account's data into it.
    The old snapshot files are not deleted, but the manifest stops pointing at them. If that data is the only copy another machine or account has, restore it or copy it elsewhere first.
pharos-daily-vault-overwrite-ok = Overwrite the folder

## Daily papers · the data folder · what it covers

# The three most important sentences in the panel. The desktop's answer differs
# from the web's, because the desktop HAS a real local library and therefore has
# to say plainly that this folder is not responsible for it.
pharos-daily-vault-scope-head = This backs up the digest, not your library
pharos-daily-vault-scope-in = Written to the folder: your research directions and filter settings -- categories, the daily cap, the on/off switch, and every direction with its keywords and order. Those exist only on the server; this machine holds no copy of them, so losing the account or moving servers means retyping every one by hand. Plus each day's paper snapshot, Chinese reading, highlights, scores and matched direction -- including the papers you never imported, which is most of any digest.
pharos-daily-vault-scope-out = Not written to the folder: your Zotero library. An imported paper's item, its PDF and its provenance note already live in the local data directory and are covered by whatever backs that up. This folder neither duplicates them nor brings them back after a disk failure. Passwords, JWTs, LLM keys, Zotero tokens and account ids are never written either.
pharos-daily-vault-scope-when = Saved automatically once each sweep finishes. After editing directions in settings, come back here and use Save now. Older snapshots in the folder are never deleted.

## Daily papers · the data folder · read and write failures

# The text of the Errors Zotero.Pharos.Daily.Vault throws. The folder is a path
# the user picked and its contents may have been edited by anything, so every
# refusal has to name what was refused.
# $path (String)
pharos-daily-vault-unsafe-path = Refused an unsafe path in the folder's manifest: { $path }
pharos-daily-vault-missing-file = The manifest names a file that is not there: { $path }
pharos-daily-vault-too-large = Refused to read an oversized file: { $path }
pharos-daily-vault-root-missing = The folder does not exist; nothing was written: { $path }
# $label (String) - which manifest entry is at fault
pharos-daily-vault-entry-missing = The manifest has no { $label } entry
pharos-daily-vault-entry-invalid = The manifest's { $label } entry is malformed
pharos-daily-vault-entry-digest = The manifest's { $label } checksum is malformed
pharos-daily-vault-checksum = { $label } failed verification; the file may be damaged or edited
pharos-daily-vault-bad-file = { $label } is not valid JSON
pharos-daily-vault-label-profile = the directions profile
pharos-daily-vault-label-day = a daily snapshot
pharos-daily-vault-bad-json = pharos-vault.json is not valid JSON
pharos-daily-vault-bad-manifest = pharos-vault.json is missing required fields
pharos-daily-vault-bad-version = This folder is not a supported Pharos data folder v1
pharos-daily-vault-bad-index = The data folder's date index is invalid
pharos-daily-vault-bad-archive = The server returned an incomplete Daily Papers snapshot
pharos-daily-vault-no-manifest = The chosen folder holds no pharos-vault.json
pharos-daily-vault-bad-profile = The folder's directions profile uses an unsupported version
# $date (String) - YYYY-MM-DD
pharos-daily-vault-bad-date = Invalid date: { $date }
pharos-daily-vault-bad-day = The daily snapshot for { $date } is malformed
pharos-daily-vault-bad-count = The paper count for { $date } does not match the manifest

## Literature discovery

pharos-discovery-menu = Find Literature…
pharos-discovery-window =
    .title = Find Literature
# pharos-discovery-window has an attribute and no value, so Zotero.getString()
# cannot read it. Name the module in running text with this one.
pharos-discovery-heading = Find Literature
pharos-discovery-subheading = Start from a research question. Every run keeps which sources it used, which of them failed, and how far the analysis actually went.
pharos-discovery-current-project = Current project

## Literature discovery · the search form

pharos-discovery-query-label = Research question or idea
pharos-discovery-placeholder =
    .placeholder = e.g. KV cache compression for long-context video generation
pharos-discovery-search = Run Search
pharos-discovery-searching = Searching…
# This has to be honest: nothing has been read when Run Search returns. Both
# providers hand back metadata and abstracts, and a model is only ever called
# one paper at a time, from the button on that paper's card.
pharos-discovery-hint = Type a research question and press Run Search. arXiv and OpenAlex return metadata and abstracts only; a Chinese key idea is produced one paper at a time, and only when you ask for one.
pharos-discovery-language-hint = arXiv works best with English keywords. Chinese is sent exactly as typed and is not translated.

pharos-discovery-sources-label = Sources
pharos-discovery-source-arxiv = arXiv
pharos-discovery-source-arxiv-note = Preprints and the newest work
pharos-discovery-source-openalex = OpenAlex
pharos-discovery-source-openalex-note = Across publishers, with citation counts
pharos-discovery-source-unknown = Unknown source
# Joins several source names. The value is a comma and a trailing space, and
# Fluent strips whitespace after "=", hence the string literal. zh-CN uses an
# ideographic comma and does not need one.
pharos-discovery-source-separator = { ", " }

pharos-discovery-project-label = Linked project
pharos-discovery-project-none = Not linked
# $name (String) - the project's name
pharos-discovery-project-archived = { $name } (archived)
pharos-discovery-limit-label = Results
pharos-discovery-projects-failed = Could not load your projects. Searching still works, but results cannot be filed anywhere yet.

## Literature discovery · form validation

# $min (Number) - the fewest characters a query may have
pharos-discovery-need-query = A search needs at least { $min } characters.
# $max (Number) - the most characters a query may have
pharos-discovery-query-too-long = A search can be at most { $max } characters.
pharos-discovery-need-source = Choose at least one source.
# $min (Number)
# $max (Number)
pharos-discovery-limit-range = Results has to be a whole number between { $min } and { $max }.
# $error (String) - what the transport or the server gave as the reason
pharos-discovery-search-failed = The search request failed: { $error }

## Literature discovery · past runs

pharos-discovery-history-head = Past runs
pharos-discovery-history-loading = Loading history…
pharos-discovery-history-empty = Once a search has run, its results can be reopened at any time.
pharos-discovery-history-failed = Could not load the search history.
# $time (String) - already formatted by the window with Intl.DateTimeFormat.
# A plain string, not a Fluent DATETIME(); do not turn it into a date argument.
# $count (Number) - results that run left behind
pharos-discovery-history-meta = { $time } · { $count } papers
pharos-discovery-retry = Retry
# Only ever seen when a history row arrived without its results and the detail
# has to be fetched. Nothing on screen used to change while that request was in
# flight -- the previous run stayed up, with no way to tell the click landed.
pharos-discovery-opening = Opening this run…
# $error (String)
pharos-discovery-open-failed = Could not open this run: { $error }

## Literature discovery · run status

pharos-discovery-status-complete = Complete
pharos-discovery-status-partial = Partial
pharos-discovery-status-error = Failed
# Deliberately "Unfinished", not "Searching": POST /api/discovery/search is
# synchronous and commits only on success, so a persisted running row is a
# request that died, not one still in flight.
pharos-discovery-status-running = Unfinished
pharos-discovery-status-running-hint = This run never finished, so its results may be incomplete. Running it again creates a new record and leaves this one alone.
# $count (Number) - results this run kept
# $sources (String) - source names, already joined by the window with
#   pharos-discovery-source-separator
# $time (String) - pre-formatted, as in pharos-discovery-history-meta
pharos-discovery-run-meta = { $count } papers · { $sources } · { $time }
pharos-discovery-reuse = Reuse These Settings
pharos-discovery-reused = The old settings are back in the form. Adjust them and run again.
pharos-discovery-errors-partial = Some sources did not return
pharos-discovery-errors-all = Source errors

## Literature discovery · outcome notices

# $count (Number) - candidate papers found
pharos-discovery-notice-complete = Found { $count } candidate papers.
# $count (Number) - results still usable
pharos-discovery-notice-partial = Some sources returned. { $count } usable results were kept.
pharos-discovery-notice-error = Every source failed. The run was saved, and can be reopened from the list on the left.

## Literature discovery · filing into a project

pharos-discovery-select-all = Select All
# $count (Number) - papers ticked
pharos-discovery-selected = { $count } selected
pharos-discovery-add-to-project = Add to Project
pharos-discovery-adding = Adding…
# Labels the project dropdown. No longer a modal question.
pharos-discovery-pick-project = Choose a project
pharos-discovery-new-project = New Project
pharos-discovery-new-project-head = Create a project and file the selected papers
pharos-discovery-new-project-desc = The new project becomes the current one.
pharos-discovery-new-project-name =
    .placeholder = Project name
pharos-discovery-new-project-question =
    .placeholder = Research question (optional)
# $count (Number) - papers that will be filed along with it
pharos-discovery-new-project-create = Create and add { $count }
pharos-discovery-new-project-creating = Creating…
pharos-discovery-need-project = Choose a project first.
pharos-discovery-need-selection = Select the papers to file first.
pharos-discovery-filed = Already in this project
# $name (String) - the project filed into
# $added (Number) - papers added this time
pharos-discovery-file-result = Filed into “{ $name }”: { $added } added
# $count (Number) - papers already there, skipped this time.
# Appended to the line above only when the count is above zero; the full stop is
# added by the window. Same pattern as pharos-daily-sweep-failed.
pharos-discovery-file-skipped = , { $count } already there
# $count (Number) - papers that failed to file, appended on the same condition
pharos-discovery-file-failed = , { $count } failed
# $error (String)
pharos-discovery-file-error = Could not file into the project: { $error }

## Literature discovery · result card

pharos-discovery-rank-tooltip = Rank within this run
# The four segments of a card's meta row. All labelled, as the web labels them:
# bare values joined by a middle dot leave no way to tell the venue from the
# provider list, and "arXiv" can legitimately be either.
# $year (String) - the publication year, already stringified by the window
# English has no year particle, so the value stands alone here. The id exists so
# that each locale decides for itself; zh-CN appends 年.
pharos-discovery-meta-year = { $year }
# $venue (String) - the journal or conference
pharos-discovery-meta-venue = In: { $venue }
# $sources (String) - the providers that corroborated this paper, already joined
#   by the window with pharos-discovery-source-separator. NOT the run's own
#   requested source list.
pharos-discovery-meta-sources = Sources: { $sources }
# $count (Number) - times the paper has been cited
pharos-discovery-citations = Cited { $count } times
# Tooltip on the linked title. Zotero.launchURL hands the link to the system
# browser, so the string says where it goes.
pharos-discovery-open = Open the source page in your browser
pharos-discovery-pdf = View PDF

pharos-discovery-trick-label = Key idea
pharos-discovery-trick-pending = No Chinese key idea yet
# "Chinese", as in -trick-pending above: what a model produces here is a Chinese
# reading, and dropping the word makes an empty result read as a missing English
# one.
pharos-discovery-trick-empty = The model returned no Chinese key idea
pharos-discovery-trick-extracted-tooltip = A sentence taken straight from the abstract, unread by any model
pharos-discovery-abstract-label = Abstract (English)

pharos-discovery-section-contribution = Contribution
pharos-discovery-section-core-trick = Key idea
pharos-discovery-section-method = Method
pharos-discovery-section-results = Results
pharos-discovery-section-limitations = Limitations
# pharosDiscoveryTest.js asserts this one by equality; a reworded value fails it
# outright. "Read It" no longer matches the button (now Generate Key Idea), but
# this is note body text rather than UI chrome, so the wording drift is filed
# separately instead of breaking a pinned assertion here.
pharos-discovery-rules-note = Summarised without a model. Use "Read It" for a proper reading.
# After a deep read the backend overwrites summary_zh, contribution, core_trick,
# method and results but DELIBERATELY keeps the rules-extracted limitations
# (backend/pharos/services/projects.py:393-403). That row is still a cue-matched
# sentence copied out of the English abstract, which the model never saw. The
# card carries the "AI reading" chip by then, so without its own label the reader
# takes it for the model's assessment of the paper's weaknesses.
pharos-discovery-limitations-rules = Rule extract
pharos-discovery-limitations-rules-hint = A deep read does not replace this row: the sentence below was extracted from the English abstract by rule. No model read or assessed it.
# The provenance line written into the Zotero note. The note lives in the library
# permanently, indistinguishable from the reader's own notes, and the window that
# knew where it came from is long closed.
pharos-discovery-note-llm = Generated by { $model } from the title and abstract; the model did not read the full text.
pharos-discovery-note-llm-unknown = Generated by a language model from the title and abstract; the model did not read the full text.
pharos-discovery-note-limitations = The Limitations entry is a rule extract, not the model's judgement.

pharos-discovery-mode-rules = Rules on the abstract
pharos-discovery-mode-llm = AI reading (Chinese)
# The localized stand-in for analysis_warning, rendered inline in the card. The
# server's own English sentence goes in the title of the same block, so the wire
# value stays inspectable without being the only surface it has.
pharos-discovery-mode-rules-detail = Sentences are extracted from the title and abstract only. No model was called, and no full text was downloaded or read. An empty field means the abstract did not say.
# Follows the sentence above. That one says where the extract came from; this
# one says where to go for a model reading -- the control that does it lives only
# in the Discovery window, so telling the reader to press it here would name a
# button that does not exist on this screen.
pharos-projects-source-rules-where = Open this paper in Discovery to generate a model reading.
# $model (String) - the model the server recorded
pharos-discovery-model = Read by { $model }
pharos-discovery-model-unknown = Reading model not recorded

pharos-discovery-analyze = Generate Key Idea
pharos-discovery-analyzing = Generating…
pharos-discovery-reanalyze = Regenerate

## Literature discovery · save to library

# Desktop only: this files a real Zotero item for the paper, which the web
# companion has no equivalent for. It owns its ids rather than borrowing
# pharos-daily-save* -- that borrowing is the cross-module coupling this rebuild
# is removing.
pharos-discovery-save = Save to Library
pharos-discovery-saving = Saving…
pharos-discovery-saved = In Library
# $error (String)
pharos-discovery-save-failed = Could not save this paper: { $error }

## Literature discovery · generation failures

# Both the 409 and the 503 say outright that the rules result survived. Dumping
# the server's raw error, which is what happens today, leaves no way to tell
# whether the earlier result was overwritten.
pharos-discovery-analyze-no-provider = No reading model is configured on the server. The rules result was kept and has not been overwritten; it can be upgraded once one is set.
pharos-discovery-analyze-provider-failed = Generating the Chinese key idea failed this time. The rules result was not overwritten; try again later.
pharos-discovery-analyze-no-abstract = This result has no abstract, so there is nothing to read.
# $error (String)
pharos-discovery-analyze-failed = Could not generate: { $error }

## Literature discovery · empty states

pharos-discovery-first-title = Start from a question you can argue about
pharos-discovery-first-desc = Every run records which providers it used, which fields were extracted, and whether a model or a rule produced them. With no model configured it falls back to rules on the abstract, and says so.
# Heading for a run that returned nothing
pharos-discovery-empty = No usable results.
# Replaces the line above as the heading when search.status == 'error'
pharos-discovery-error = The search failed.
pharos-discovery-empty-hint = Check the source errors above, or reuse the settings and loosen the keywords or add a source.

## Literature discovery · retired

# The only reader of the two below is the pre-rebuild pharosDiscovery.js, at
# lines 116 and 250: pharos-discovery-count is superseded by
# pharos-discovery-run-meta / -history-meta, and pharos-discovery-added-to-project
# by the batch filing notice. The rebuild has not landed, and deleting these now
# would make this window throw outright in en-US -- an unread string is inert,
# whereas removing one locale file and not the other is what actually breaks.
# $count (Number) - results returned
pharos-discovery-count = { $count } results
pharos-discovery-added-to-project = Added

## Analysis provenance

# Shared by both windows: discovery results and project sources both carry
# analysis_mode, and not rendering it makes a model's summary indistinguishable
# from a rule extraction.
pharos-analysis-mode-llm = AI reading
pharos-analysis-mode-rules = Rule-extracted

## Research projects

pharos-projects-menu = Research Projects…
pharos-projects-window =
    .title = Research Projects

## Research projects · stages

# The nine stage names track the web companion exactly: one project must not be
# called two different things on two clients. Note that the discovery stage is
# "Literature Discovery", which collides with the discovery module's own name
# (pharos-rail-discovery). The web has the same collision and accepted it, so
# this follows for consistency; the timeline shows the short label "Discovery",
# and only the stage dropdown and the panel kicker carry the full name.
pharos-projects-stage-discovery = Literature Discovery
pharos-projects-stage-ideation = Ideation
pharos-projects-stage-planning = Experiment Planning
pharos-projects-stage-experimentation = Experiment Execution
pharos-projects-stage-analysis = Result Analysis
pharos-projects-stage-claims = Claim Consolidation
pharos-projects-stage-drafting = Paper Draft
pharos-projects-stage-review = Adversarial Review
pharos-projects-stage-complete = Project Complete

# Short labels for the timeline nodes. Nouns, never verbs: "Run" or "Analyse" as
# a node label, sitting next to the automation notice, reads as a button.
pharos-projects-stage-discovery-short = Discovery
pharos-projects-stage-ideation-short = Ideation
pharos-projects-stage-planning-short = Planning
pharos-projects-stage-experimentation-short = Experiment
pharos-projects-stage-analysis-short = Analysis
pharos-projects-stage-claims-short = Claims
pharos-projects-stage-drafting-short = Draft
pharos-projects-stage-review-short = Review
pharos-projects-stage-complete-short = Done

# One line under each stage on the research path
pharos-projects-stage-discovery-note = Set the boundary of the problem and gather the evidence pool
pharos-projects-stage-ideation-note = Form candidate hypotheses, and the mechanisms behind them
pharos-projects-stage-planning-note = Freeze the metrics, the baselines and the stopping conditions
pharos-projects-stage-experimentation-note = Record real runs and what they produced
pharos-projects-stage-analysis-note = Explain the results, and the alternative causes
pharos-projects-stage-claims-note = Constrain the results into claims that can be traced back
pharos-projects-stage-drafting-note = Organise the narrative, the citations and the figures
pharos-projects-stage-review-note = Find the evidence gaps and the overclaims
pharos-projects-stage-complete-note = Freeze this version of the research

## Research projects · record types and statuses

# review is both a stage and a type. The stage is Adversarial Review, the type is
# Review record; they must never both render as a bare "Review", because a record
# card shows its type and its stage side by side.
pharos-projects-type-hypothesis = Hypothesis
pharos-projects-type-experiment-plan = Experiment plan
pharos-projects-type-result = Experiment result
pharos-projects-type-claim = Claim
pharos-projects-type-draft = Writing draft
pharos-projects-type-review = Review record

# A verified record is the user's own judgement, not something the platform
# checked. Bare "Verified" drops the agent; "Human-verified" reads as a kind of
# verification the platform performs. See docs/DECISIONS.md §9.
pharos-projects-status-draft = Draft
pharos-projects-status-ready = Ready
pharos-projects-status-verified = Verified by you
pharos-projects-status-rejected = Rejected

# The project's own state. Deliberately not pharos-projects-status-*: that prefix
# is the record status, and a collision would make a lookup that builds its id
# from the prefix resolve into the wrong namespace.
pharos-projects-state-active = Active
pharos-projects-state-archived = Archived

## Research projects · project list

pharos-projects-list-head = Research Projects
pharos-projects-new = New Project
pharos-projects-show-archived = Show archived
# $sources (Number) - papers this project rests on
# $records (Number) - research records written for it
pharos-projects-item-meta = { $sources } papers · { $records } records
pharos-projects-loading = Loading projects…
# The old value -- "No projects yet. Create one in the Pharos web app." -- is the
# sentence docs/DECISIONS.md §4 names as the failure: the desktop can create a
# project itself, and sending the user to the web says otherwise.
pharos-projects-empty = No research projects yet
pharos-projects-none-matched = No project matches the filter
pharos-projects-error = Could not load projects.
pharos-projects-retry = Retry

## Research projects · empty and failure states

pharos-projects-welcome-title = Start a research project you can keep moving
pharos-projects-welcome-desc = A project keeps discovery results, evidence notes, experiment plans, real results and paper claims on one traceable path.
pharos-projects-load-failed-title = Could not load this project

## Research projects · creating one

pharos-projects-create-title = New Research Project
pharos-projects-name = Project name
pharos-projects-name-input =
    .placeholder = Project name
pharos-projects-question-input =
    .placeholder = Core research question (optional)
pharos-projects-description-input =
    .placeholder = Project description (optional)
pharos-projects-create-submit = Create Project
pharos-projects-creating = Creating…
pharos-projects-cancel = Cancel
# $name (String) - the project just created
pharos-projects-created = Project "{ $name }" created

## Research projects · header and lifecycle

pharos-projects-question = Core research question
pharos-projects-description = Project description
pharos-projects-edit = Edit
pharos-projects-save = Save Project
pharos-projects-saving = Saving…
pharos-projects-updated = Project updated
pharos-projects-archive = Archive
pharos-projects-restore = Restore
pharos-projects-delete = Delete
pharos-projects-delete-confirm = Delete the whole project?
pharos-projects-delete-submit = Delete Permanently
pharos-projects-deleted = Project deleted
# $date (String) - already formatted by the window
pharos-projects-meta-created = Created { $date }
# $date (String)
pharos-projects-meta-updated = Updated { $date }

## Research projects · research path

pharos-projects-path = Research path
pharos-projects-stage-select =
    .aria-label = Change the project's stage
pharos-projects-stage-save = Save Stage
pharos-projects-advance = Next Stage
pharos-projects-advancing = Advancing…
# $stage (String) - the stage advanced to
pharos-projects-advanced = Advanced to "{ $stage }"
# $count (Number) - records filed at that stage
pharos-projects-stage-count = { $count } records
pharos-projects-stage-count-none = No records
# The one control in this window that could be read as "run this stage", so §9
# is carried right next to it.
pharos-projects-stage-help = Click a stage to see its records. The dropdown moves the project back explicitly. All of this changes only what the project records about itself; none of it starts an experiment.

## Research projects · project literature

pharos-projects-sources-head = Project literature
# $count (Number) - papers this project rests on
pharos-projects-sources = { $count } evidence sources
pharos-projects-sources-empty-title = No project literature yet
pharos-projects-sources-empty-desc = Search in Find Literature and add a paper to this project.
pharos-projects-source-note = Evidence note
pharos-projects-source-note-empty = Add why it belongs here, or what it supports
pharos-projects-source-note-input =
    .placeholder = Why is this paper in the project? What does it support or contradict?
pharos-projects-source-note-save = Save Note
pharos-projects-source-note-saved = Evidence note saved
# $date (String) - already formatted by the window
pharos-projects-source-added = Added { $date }
pharos-projects-source-remove = Remove
pharos-projects-source-remove-confirm = Remove from the project?
pharos-projects-source-removed = Removed from the project. The discovery history is kept.
# Writes a record out as a real Zotero note. This is the module's reason for
# existing, and the web companion has no equivalent.
pharos-projects-save-note = Save as Note

## Research projects · research records

# $count (Number) - records written for this project
pharos-projects-artifacts = { $count } research records
pharos-projects-artifact-new = New Record
pharos-projects-artifact-new-title = New Research Record
pharos-projects-artifact-edit-title = Edit Research Record
pharos-projects-artifact-stage = Stage
pharos-projects-artifact-type = Type
pharos-projects-artifact-status = Status
pharos-projects-artifact-title = Title
pharos-projects-artifact-title-input =
    .placeholder = One line saying what this record is
pharos-projects-artifact-body = Body
pharos-projects-artifact-body-input =
    .placeholder = Record a hypothesis, an experiment's constraints, a real result, a claim or a review. Do not write a plan as though it had already run.
pharos-projects-artifact-save = Save Record
pharos-projects-artifact-saved = Research record saved
# Shown instead of the line above when status == 'verified', so that the moment
# the claim is made, it says whose judgement it was.
pharos-projects-artifact-saved-verified = Record saved and marked as verified by you
pharos-projects-artifact-delete = Delete
pharos-projects-artifact-delete-confirm = Delete this record?
pharos-projects-artifact-deleted = Research record deleted
# $date (String)
pharos-projects-artifact-updated = Updated { $date }
pharos-projects-artifacts-empty-title = No records at this stage yet
pharos-projects-artifacts-empty-desc = Write a real research record. Pharos will not claim on your behalf that an experiment was run.

## Research projects · retired

# The only reader of pharos-projects-none is the pre-rebuild pharosProjects.js,
# at line 173. Every empty state now has copy of its own, and this one must not
# be reused: it is what made the "no stage starts an experiment" notice read as
# "nothing here". Kept until the rebuild lands, because deleting it makes the
# window throw outright in en-US.
pharos-projects-none = Nothing here yet.

## Module rail

pharos-rail-library = Library
pharos-rail-library-tooltip =
    .title = Library
pharos-rail-daily = Daily Papers
pharos-rail-daily-tooltip =
    .title = Daily Papers
pharos-rail-discovery = Find Literature
pharos-rail-discovery-tooltip =
    .title = Find Literature
pharos-rail-projects = Research Projects
pharos-rail-projects-tooltip =
    .title = Research Projects
pharos-rail-collapse =
    .title = Collapse
    .aria-label = Collapse the module rail
pharos-rail-expand =
    .title = Expand
    .aria-label = Expand the module rail
pharos-rail-resize =
    .aria-label = Resize the module rail

## Administrator console

pharos-admin-menu = Admin Console…
pharos-admin-window =
    .title = Admin Console

pharos-rail-admin = Admin
pharos-rail-admin-tooltip =
    .title = Admin Console

pharos-admin-tab-users = Users
pharos-admin-tab-providers = API Configuration
pharos-admin-search =
    .placeholder = Search email or name…
    .aria-label = Search users

pharos-admin-loading = Loading…
pharos-admin-error = Could not load the console.
pharos-admin-forbidden = This account is not an administrator.
# Stands in for a value the server did not report
pharos-admin-none = —

pharos-admin-stat-users = Users
# $count (Number) - accounts with administrator rights
pharos-admin-stat-admins = { $count } administrators
pharos-admin-stat-papers = Papers
# $count (Number) - papers with a finished translation
pharos-admin-stat-translated = { $count } translated
pharos-admin-stat-projects = Projects
pharos-admin-stat-daily = Daily papers
# $count (Number) - literature searches run on this instance
pharos-admin-stat-searches = { $count } searches
pharos-admin-registration-open = Registration is open
pharos-admin-registration-closed = Registration is closed
pharos-admin-registration-hint = · set on the server, in .env

pharos-admin-column-user = User
pharos-admin-column-papers = Papers
pharos-admin-column-projects = Projects
pharos-admin-column-highlights = Highlights
pharos-admin-column-created = Registered
pharos-admin-column-last-login = Last seen
pharos-admin-column-role = Role
pharos-admin-column-actions = Actions

pharos-admin-users-empty = No accounts yet.
pharos-admin-users-none-matched = No accounts matched.
# $shown (Number) - accounts on this page
# $total (Number) - accounts in total
pharos-admin-users-truncated = Showing { $shown } of { $total }. Search to narrow the list.

pharos-admin-role-admin = Administrator
pharos-admin-role-user = User
pharos-admin-suspended = Deactivated
pharos-admin-self = you
pharos-admin-self-note = Current account
pharos-admin-promote = Make Administrator
pharos-admin-demote = Remove Administrator
pharos-admin-deactivate = Deactivate
pharos-admin-activate = Restore
pharos-admin-delete = Delete
# $email (String) - the account this button would delete
pharos-admin-delete-tooltip = Delete { $email }
pharos-admin-update-failed = Could not update this account.

pharos-admin-delete-title = Delete account
# $email (String) - the account being deleted
pharos-admin-delete-body = This permanently deletes { $email } and everything it owns.
# $papers, $projects, $highlights (Number) - what deletion destroys
pharos-admin-delete-owns = { $papers } papers, { $projects } projects, { $highlights } highlights.
pharos-admin-delete-irreversible = This cannot be undone.
pharos-admin-delete-prompt = Type the account's email address to confirm:
pharos-admin-delete-confirm = Delete Permanently
pharos-admin-deleting = Deleting…
pharos-admin-cancel = Cancel
pharos-admin-delete-failed = Could not delete this account.

pharos-admin-providers-note = API keys are configured in the server's .env and shared by every account. This page is read-only: changing a key means editing that file and restarting. The key itself never leaves the server.
# $configured (String) - the translator the server was told to use
# $effective (String) - the engine actually translating
pharos-admin-providers-degraded = Translation has degraded: { $configured } is configured, but { $effective } is what runs. The key is usually missing or invalid.
pharos-admin-role-translate = Translation
pharos-admin-role-chat = AI Chat
pharos-admin-providers-empty = No providers are configured.
pharos-admin-provider-configured = Configured
pharos-admin-provider-unconfigured = Not configured
pharos-admin-provider-model = Model
pharos-admin-provider-url = Address
pharos-admin-provider-key = Key
# $hint (String) - the key's last four characters
pharos-admin-provider-key-set = Set · ending { $hint }
pharos-admin-provider-key-unset = Not set
pharos-admin-provider-used-translate = Used for translation
pharos-admin-provider-used-chat = Used for chat
pharos-admin-probe = Test Connection
pharos-admin-probing = Testing…
# $ms (Number) - round-trip time of the test completion
pharos-admin-probe-ok = Working · { $ms } ms
pharos-admin-probe-failed = The test failed.

# =============================================================================

## Daily paper settings

pharos-prefs-daily-pane = Daily Papers
# On the Pharos pane, opening the subpane. A XUL button, hence .label.
pharos-prefs-daily-open =
    .label = Daily Paper Settings…

pharos-prefs-daily-signed-out = Sign in to your Pharos account to edit your research directions.

# The one thing about this screen that cannot be guessed from the controls.
pharos-prefs-daily-note = Your directions are yours; the sweep is shared. Editing a direction takes effect immediately -- the next digest you open is re-matched and re-ranked, with nothing fetched again and nothing read again. arXiv categories are different: they decide which papers are fetched at all, and that request is made once for everybody. A category you add starts working from the next sweep onward, and days already fetched will not fill in.

pharos-prefs-daily-directions-header = Research Directions
# $count (Number) - directions this account has
# $max (Number) - the most it may have
pharos-prefs-daily-count = { $count } of { $max }
pharos-prefs-daily-loading = Loading…
pharos-prefs-daily-load-failed = Could not load your research directions.

pharos-prefs-daily-empty-title = No research directions yet
pharos-prefs-daily-empty-desc = The daily digest is filtered entirely by your directions: with none, it is empty, and not one fetched paper is shown. Add one, or put the seven defaults back and edit them from there.
pharos-prefs-daily-restore = Restore Default Directions
pharos-prefs-daily-restoring = Restoring…
pharos-prefs-daily-restore-none = None of the default directions could be restored. Add one by hand instead.

pharos-prefs-daily-add = New Direction
pharos-prefs-daily-edit = Edit
pharos-prefs-daily-delete = Delete
pharos-prefs-daily-delete-confirm = Confirm Delete
pharos-prefs-daily-deleting = Deleting…
pharos-prefs-daily-enabled = Enabled
pharos-prefs-daily-disabled = Disabled
pharos-prefs-daily-move-up = Move Up
pharos-prefs-daily-move-down = Move Down
pharos-prefs-daily-order-help = Order is the tie-break when a paper matches several directions, so it decides which one a paper is filed under.

pharos-prefs-daily-name = Name
pharos-prefs-daily-name-input =
    .placeholder = Direction name, for example VLA
pharos-prefs-daily-keywords = Keywords
pharos-prefs-daily-keywords-input =
    .placeholder = One per line, or separated by commas
pharos-prefs-daily-save = Save
pharos-prefs-daily-saving = Saving…
pharos-prefs-daily-create = Create
pharos-prefs-daily-cancel = Cancel

# Keyword syntax. Load-bearing: what is typed is what is matched.
pharos-prefs-daily-syntax-help = A term matches when it appears anywhere in a paper's title or abstract, spaces and punctuation included. Wrap it in double quotes -- "wam" -- to match it as a whole word instead, so it fires on "WAM:" but never inside "swam". What you type is sent as you typed it.
pharos-prefs-daily-parsed-none = No keywords yet. A direction with no keywords matches nothing at all.
# $count (Number) - distinct terms the direction will be matched on
pharos-prefs-daily-parsed-count = Matching on these { $count } terms; a paper is a hit if any one of them appears.
pharos-prefs-daily-chip-word = whole word
pharos-prefs-daily-chip-substring = anywhere in the text

# $max (Number)
# $count (Number)
pharos-prefs-daily-warn-keyword-count = Too many keywords (at most { $max }, currently { $count }).
# $max (Number)
# $count (Number)
pharos-prefs-daily-warn-keyword-total = The keyword list is too long (at most { $max } characters, currently { $count }).
# $count (Number) - keywords over the per-keyword limit
# $max (Number)
pharos-prefs-daily-warn-keyword-long = { $count } keywords are longer than { $max } characters. A whole sentence is unlikely ever to appear verbatim.

pharos-prefs-daily-sweep-header = What Gets Fetched
# The module's master switch. The digest's own "switched off" empty state sends
# the user to this pane and tells them to turn it back on here -- without this
# control that instruction was wrong and the state was unrecoverable.
#
# Not named -enabled: that id already exists as a single direction's state label
# ("Enabled").
pharos-prefs-daily-module-on = Enable Daily Papers
pharos-prefs-daily-module-on-help = Off means nothing is fetched and nothing is read. Existing digests and imported papers are kept.
pharos-prefs-daily-categories = arXiv categories
# $max (Number)
pharos-prefs-daily-categories-help = These decide which papers are fetched each day. Directions can only filter what was fetched, so no number of keywords will surface a paper from outside them. Separate with commas or spaces; at most { $max }.
# $list (String) - the tokens that did not look like categories
pharos-prefs-daily-categories-invalid = These do not look like arXiv categories: { $list }
# $max (Number)
# $count (Number)
pharos-prefs-daily-categories-too-many = Too many categories (at most { $max }, currently { $count }).
pharos-prefs-daily-max = Papers per day
# $min (Number)
# $max (Number)
pharos-prefs-daily-max-help = How many papers a day is allowed to keep ({ $min }–{ $max }). When more match, the highest-scoring ones are kept.
# $min (Number)
# $max (Number)
pharos-prefs-daily-max-range = The daily limit has to be a whole number between { $min } and { $max }.
pharos-prefs-daily-max-blank = Left empty, the daily limit is not changed.
pharos-prefs-daily-config-save = Save Fetch Settings
pharos-prefs-daily-config-revert = Revert
pharos-prefs-daily-config-saved = Saved.
pharos-prefs-daily-config-failed = Could not load the fetch settings.

## AI model

pharos-prefs-provider-header = AI Model
pharos-prefs-provider-loading = Loading…
pharos-prefs-provider-failed = Could not load the model settings.
pharos-prefs-provider-source-personal = Your own model
pharos-prefs-provider-source-server = Server model
pharos-prefs-provider-source-none = Not configured
pharos-prefs-provider-address = Address
pharos-prefs-provider-model = Model
pharos-prefs-provider-temperature = Temperature
pharos-prefs-provider-max-tokens = Maximum output tokens
pharos-prefs-provider-key-stored = An API key is stored for this account.
pharos-prefs-provider-key-none = No API key of your own is stored.
pharos-prefs-provider-key-unsupported = This server has no credential encryption key configured, so it cannot hold a personal API key. Only the model the administrator provides is available.
# Says plainly what this pane will and will not do with a key.
pharos-prefs-provider-security = Pharos never keeps an API key on this computer -- not in settings, not in the log, nowhere. Keys are entered in the Pharos web app and encrypted by the server. This pane can show which model is in use and clear it; it cannot read the key back.
pharos-prefs-provider-clear = Clear My Model
pharos-prefs-provider-clearing = Clearing…
pharos-prefs-provider-clear-confirm = Delete the personal model settings and API key stored for this account? Conversations you have already had are kept.
pharos-prefs-provider-cleared = Your model settings were cleared.
pharos-prefs-provider-cleared-server = Your model settings were cleared. The server's model is in use again.
pharos-prefs-provider-clear-failed = Could not clear the model settings.


# =============================================================================

## Appearance

pharos-prefs-appearance-pane = Appearance

pharos-prefs-appearance-scheme-header = Color Scheme
pharos-prefs-appearance-scheme = Color scheme:
pharos-prefs-appearance-scheme-auto =
    .label = Automatic
pharos-prefs-appearance-scheme-light =
    .label = Light
pharos-prefs-appearance-scheme-dark =
    .label = Dark
pharos-prefs-appearance-scheme-help = Automatic follows the operating system. This is the same setting as the one under General.

pharos-prefs-appearance-accent-header = Accent Color
pharos-prefs-appearance-accent-help = Used only where it counts: selected rows, active icons, focus rings and links.
pharos-prefs-appearance-accent-group =
    .aria-label = Accent color
pharos-prefs-appearance-accent-note = In the light theme every accent is deepened so that it stays readable as text on the paper background.

pharos-prefs-appearance-accent-pharos = Pharos Blue
pharos-prefs-appearance-accent-beacon = Beacon Gold
pharos-prefs-appearance-accent-mint = Mint
pharos-prefs-appearance-accent-sky = Sky
pharos-prefs-appearance-accent-pine = Pine
pharos-prefs-appearance-accent-indigo = Indigo
pharos-prefs-appearance-accent-lilac = Lilac
pharos-prefs-appearance-accent-coral = Coral
pharos-prefs-appearance-accent-amber = Amber
pharos-prefs-appearance-accent-stone = Stone

pharos-auth-window =
    .title = Sign in to Pharos

pharos-auth-tagline = One workbench for the whole arc, from finding a paper to moving the research forward

# Shown over the brand panel when the poster artwork is missing.
pharos-auth-poster-sub = Lighting the sea of literature

pharos-auth-mode-login = Sign In
pharos-auth-mode-register = Register

pharos-auth-email = Email
pharos-auth-email-placeholder =
    .placeholder = you@example.com
pharos-auth-email-required = Enter your email
pharos-auth-email-invalid = That does not look like an email address

pharos-auth-password = Password
pharos-auth-password-placeholder =
    .placeholder = ••••••••
# $min (Number) - the backend's minimum password length
pharos-auth-password-placeholder-register =
    .placeholder = At least { $min } characters
pharos-auth-password-required = Enter your password
# $min (Number) - the backend's minimum password length
pharos-auth-password-short = Password must be at least { $min } characters

pharos-auth-display-name = Display name
pharos-auth-display-name-optional = · optional
pharos-auth-display-name-placeholder =
    .placeholder = Leave blank to use your email

pharos-auth-submit-sign-in = Sign In
pharos-auth-submit-register = Create Account
pharos-auth-submitting-sign-in = Signing in…
pharos-auth-submitting-register = Creating…

pharos-auth-register-note = Registering gives you your own library · only you can see your papers and translations
pharos-auth-registration-closed = Registration is closed on this instance · sign in with an existing account

# The way past the gate. Everything local -- the library, the reader,
# annotations -- works without an account.
pharos-auth-skip = Skip for now
pharos-auth-skip-note = The library, the reader and annotations work without an account. You can sign in later in Settings → Pharos.

# One line, chosen by the local date. Quiet encouragement for someone opening
# this at the start of a long reading day -- nothing congratulates the user for
# showing up, and nothing promises what the product will do for them.
pharos-auth-greeting-0 = May one of today's papers light up exactly where you are stuck
pharos-auth-greeting-1 = The reading list never ends. One paper at a time
pharos-auth-greeting-2 = Slower is fine. Understanding beats finishing
pharos-auth-greeting-3 = A good question is rarer than a good answer
pharos-auth-greeting-4 = Still moving forward today, even if only a little
pharos-auth-greeting-5 = With the lamp lit, the sea is not so dark
pharos-auth-greeting-6 = What you are doing is worth doing slowly
pharos-auth-greeting-7 = Read one paper first. The rest can wait

# Shown in place of an address when no one is signed in.
pharos-rail-account-none = Not signed in
pharos-rail-account-settings = Settings & account
# Sub-label of the signed-out footer. It says what the button does, because
# signing in is the only thing worth doing from that state.
pharos-rail-account-sign-in = Sign in
# Names the button when the rail is collapsed and the labels are hidden.
pharos-rail-account-tooltip =
    .title = Settings & account
pharos-rail-account-sign-in-tooltip =
    .title = Sign in to Pharos

## The item pane's translation section.
##
## The first two are attribute-only (.label / .tooltiptext); Zotero builds both
## ids from data-pane. Everything else is a VALUE message -- the section reads
## them with formatValueSync, which returns null for an attributes-only message.

section-pharos-translate =
    .label = Translation
sidenav-pharos-translate =
    .tooltiptext = Translation

# "No translation here", not "Not translated". This section can see only the
# local library; a translation made in the web client or on another machine is
# invisible to it, so "Not translated" would be a claim about the account made
# from the evidence of one library.
# The item-tree column header. The column is narrow and carries a value only
# when something actually happened -- see Translate.stateLabel().
pharos-translate-column-state = Translation
pharos-translate-state-unknown = No translation here
pharos-translate-state-unknown-detail = This reflects this library only. A translation made in the web client or on another device does not appear here.
pharos-translate-state-is-translation = This is a translation
pharos-translate-state-translating = Translating
pharos-translate-state-translating-percent = Translating · { $percent }%
pharos-translate-state-translated = Translated
pharos-translate-state-failed = Failed

# The engine's own stage label is free text, and some of them are long and sit
# unchanged for minutes, which reads as a hang. These three are the web client's
# mapping term for term -- one job must not be described as being at two
# different steps in two windows.
pharos-translate-stage-parse = Parsing layout
pharos-translate-stage-translate = Translating text
pharos-translate-stage-typeset = Rebuilding layout
pharos-translate-stage-tooltip = Engine stage: { $stage }

pharos-translate-action-open = Open Translation
pharos-translate-action-open-named = Open { $name }
pharos-translate-action-open-original = Open Original
pharos-translate-action-retry = Retry
# The progress dialog is the only place a running job can be cancelled, so this
# is not merely "see the progress".
pharos-translate-action-queue = Show Progress
