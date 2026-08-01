# Decisions

Load-bearing decisions and the reasons behind them, newest last. Append rather
than edit: a decision that turned out wrong is more useful superseded in place
than deleted, because the next person will otherwise reach the same wrong
conclusion by the same route.

A change that reverses one of these is a conversation, not a commit.

---

## 1. Pharos is the whole research arc, not a translator

Discover → read → translate → organise → write. Translation and reading is the
flagship built module; writing is roadmap.

**Why it is written down:** work has drifted toward "a translator with a library
attached" more than once. The framing decides what belongs in the product at
all, so getting it wrong misdirects everything downstream.

## 2. The translation engine runs as an arm's-length subprocess

BabelDOC runs in `engine_worker/`, a separate process the backend talks to over
NDJSON, never in-process and never in a client.

**Why:** two independent reasons that happen to agree. It is AGPL, and process
separation is the aggregation seam that keeps the licence boundary clean. And it
carries native dependencies — on Apple Silicon it needs Rosetta and a specific
conda toolchain — which would otherwise be everyone's problem. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) §3.

## 3. The desktop client is built from Zotero source, not alongside it

`client/` is a manual copy of the Zotero repository with `.git` removed. No fork
relationship, no submodule, no upstream remote.

**Why:** the alternative was reimplementing a library, a PDF reader with
annotations, citation styles, and 760+ web translators — a decade of work — to
put a translate button next to it. Building from source means those are Zotero's
own, and Pharos adds only what Zotero does not have.

**Why a copy rather than a fork:** the project decided to sever the upstream
relationship permanently. Upstream fixes can still be applied by hand; the
baseline for comparison is recorded in `client/UPSTREAM.txt`.

**Cost, accepted knowingly:** upstream fixes are manual, and the client's
peculiarities have to be learned. `client/BRANDING.md` records the ones that
fail silently.

**Supersedes:** the Tauri desktop client, removed in `7f9e63a`. It reimplemented
around a shared React bundle what Zotero had already solved. Its Workspace,
Codex bridge and deep links went with it and are not currently replaced.

## 4. Reading belongs in the client, writing in the web app

Anything that happens while reading a paper — translation, chat about it,
annotation — belongs in `client/`. Writing, administration, and configuration at
scale belong in `frontend/`. Anything needing a model, a key, or a database
belongs in `backend/`.

**Why:** the two surfaces have different shapes. A reference manager is built
around a library and a document; a writing environment is built around a
document being produced. Putting each in the wrong one makes both worse, and it
is the most common way a feature ends up in the wrong place.

## 5. Pharos keeps its own library, in `~/Pharos`

The desktop client's data directory is `~/Pharos`, its database is
`pharos.sqlite`, its bundle identifier is `top.selab.pharos`, and its URL scheme
is `pharos://`.

**Why:** the developer, and any user who tries Pharos, may have Zotero
installed. Sharing a data directory, a database filename, or a URL scheme means
two applications contending for the same files.

**How it was found:** the opposite. Before `CLIENT_NAME` was changed, a
development build resolved its data directory to `~/Zotero` and opened the
developer's real library. It failed only because another process held the SQLite
lock. `-profile` isolates the Gecko profile, **not** Zotero's data directory —
a distinction that is easy to assume away.

`app/scripts/run_pharos_dev` exists so that this cannot recur: it passes
`-datadir` explicitly and refuses `~/Zotero` outright.

## 6. Automatic updates are disabled, and builds are unsigned

`[AppUpdate]` is commented out in `app/assets/application.ini`, and
`DEVELOPER_ID` is empty.

**Why updates are off:** it pointed at Zotero's update server, which would serve
an official Zotero build as an "update" and silently replace Pharos. Restoring
it requires Pharos hosting its own endpoint.

**Why unsigned:** there is no Apple Developer account. The previous value was
Zotero's own certificate hash — naming a certificate this project cannot use and
does not own is worse than being honestly unsigned. The build skips notarisation
cleanly and says so; setting `DEVELOPER_ID` and `NOTARIZATION_*` in
`app/config-custom.sh` is all that is needed once an account exists.

## 7. Tokens live in the OS credential store, keys never reach the browser

The desktop client stores its bearer token in the login manager, encrypted
through OSKeyStore, under its own login host rather than sharing Zotero's. Model
API keys are encrypted server-side with `PHAROS_CREDENTIAL_SECRET` and are never
returned to browser JavaScript.

**Why the separate login host:** two unrelated credentials in one bucket means
clearing one clears the other.

**Why 401 clears the token:** an expired or revoked token otherwise becomes a
permanent wall of failures with no way back to a sign-in prompt.

## 8. Zotero import is one-way and metadata-only

Pharos imports from Zotero Cloud. It does not write back, and Local API
write-back stays disabled.

**Why:** two systems both claiming to own the same library is a data-loss shape.
Zotero stays authoritative for what came from Zotero.

## 9. Pharos records research; it does not run it

Projects persist hypotheses, plans, results and claims that a researcher
supplied. No code is executed, no compute allocated, no metric validated. A
`verified` record is a user's judgement.

**Why it is stated in the product and not just here:** the backend returns an
`automation_notice` saying so, and every client carries it verbatim rather than
paraphrasing. A record that reads like an experiment result, detached from the
caveat that nothing executed it, is exactly the misreading this prevents.

## 10. The client's own `.gitignore` rules must stay anchored

Repository-root patterns like `data/` and `dist/` match at any depth. They are
anchored or listed per directory.

**Why:** unanchored, `data/` matched `client/chrome/content/zotero/xpcom/data/`
— the desktop client's entire data model — and every test fixture beneath
`client/test/tests/data`. 134 tracked files. They were already committed and so
not lost, but any later edit to one of them would have been skipped by `git add`
without a word.
