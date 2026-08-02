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
pharos-discovery-add-to-project = Add to Project
pharos-discovery-pick-project = Which project should this paper support?
pharos-discovery-added-to-project = Added

pharos-discovery-section-contribution = Contribution
pharos-discovery-section-core-trick = Key idea
pharos-discovery-section-method = Method
pharos-discovery-section-results = Results
pharos-discovery-section-limitations = Limitations
pharos-discovery-rules-note = Summarised without a model. Use "Read It" for a proper reading.

## Research projects

pharos-projects-menu = Research Projects…
pharos-projects-window =
    .title = Research Projects
pharos-projects-loading = Loading…
pharos-projects-empty = No projects yet. Create one in the Pharos web app.
pharos-projects-error = Could not load projects.
pharos-projects-none = Nothing here yet.
pharos-projects-question = Research question
pharos-projects-advance = Advance Stage
pharos-projects-save-note = Save as Note
# $count (Number) - papers this project rests on
pharos-projects-sources = Sources ({ $count })
# $count (Number) - records written for this project
pharos-projects-artifacts = Records ({ $count })

pharos-projects-stage-discovery = Discovery
pharos-projects-stage-ideation = Ideation
pharos-projects-stage-planning = Planning
pharos-projects-stage-experimentation = Experimentation
pharos-projects-stage-analysis = Analysis
pharos-projects-stage-claims = Claims
pharos-projects-stage-drafting = Drafting
pharos-projects-stage-review = Review
pharos-projects-stage-complete = Complete

pharos-projects-type-hypothesis = Hypothesis
pharos-projects-type-experiment-plan = Experiment plan
pharos-projects-type-result = Result
pharos-projects-type-claim = Claim
pharos-projects-type-draft = Draft
pharos-projects-type-review = Review

pharos-projects-status-draft = Draft
pharos-projects-status-ready = Ready
pharos-projects-status-verified = Verified
pharos-projects-status-rejected = Rejected

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
