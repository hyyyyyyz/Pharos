# Phase: the first downloadable build

The point at which Pharos stopped being something you run from a checkout and
became something you download, install, and double-click. That transition
exposed a class of bug the project had no way to see before, and this document
is mostly about that class.

## What shipped

`.github/workflows/desktop-release.yml` builds three platforms on a
`desktop-v*` tag: a macOS `.dmg` on `macos-14`, and Windows and Linux portable
archives cross-built on `ubuntu-latest`. The release job attaches whatever the
platform jobs produced, guarded by `if: always() && ... == 'success'` so a
single failing platform does not discard the two that worked.

Version 1.0.0. The client's `version` file carries the `.SOURCE` suffix
(`1.0.0.SOURCE`) because `prepare_build` matches `/([0-9].+)\.SOURCE/` to find
the version at all and substitutes the suffix per channel; a bare `1.0.0` fails
with "Version number not found".

## The bug that only a released build could have

The installed `.dmg` died on launch with no window and no message:

```
Exception Type:  EXC_BAD_ACCESS (SIGKILL (Code Signature Invalid))
Termination Reason: Namespace CODESIGNING, Code 2, Invalid Page
Thread 0 Crashed: ... dyld4::APIs::dlopen + 120
```

**Cause.** `app/build.sh` patches the update channel into
`ChannelPrefs.framework/ChannelPrefs` — a Mach-O binary Mozilla ships already
signed — but only when the channel is not `source`:

```bash
if [ "$UPDATE_CHANNEL" != "source" ]; then
	"$CALLDIR/mac/set-channel-prefs-channel" .../ChannelPrefs source $UPDATE_CHANNEL
fi
```

Editing bytes inside a signed binary invalidates the signature it still
carries. The only code that repaired this lived inside `if [ $SIGN == 1 ]`,
which requires a `DEVELOPER_ID` this project does not have — upstream Zotero
always signs its releases, so upstream never reaches the broken state.

On Apple Silicon a **present-but-invalid** signature is fatal in a way an
absent one is not. The kernel validates each page as it is faulted in and kills
the process outright when a hash does not match, which is why the failure lands
inside `dlopen` with a kernel termination rather than a loader error.

**Why no local build ever caught it.** Every local build defaults to the
`source` channel, so the patch above never runs and Mozilla's signature stays
intact. `app/scripts/dir_build` also calls `codesign_local`, which reinforced
the impression that local and CI differed only in signing. They differed in
*channel*, and the channel was what broke the binary.

**Fix.** Re-sign ad-hoc at the site of the damage, and only when a real signing
pass is not coming:

```bash
if [ $SIGN != 1 ]; then
	/usr/bin/codesign --force --options runtime --sign - \
		"$CONTENTSDIR/Frameworks/ChannelPrefs.framework"
fi
```

Guarded on `uname -s` because build.sh's macOS branch also runs on Linux for
cross-builds, where there is no `codesign`; that path warns loudly instead of
silently producing a bundle that cannot launch.

A second, quieter instance of the same rule was fixed alongside it:
`libZoteroWordIntegration.dylib` is built here rather than shipped by Mozilla,
so it arrives with **no** signature — also unloadable on Apple Silicon, though
it fails later and only takes Word integration with it. `codesign_local` signs
it for `dir_build`; build.sh now does the same for builds that go straight to a
package, which is every build CI produces.

## How it was found

Worth recording, because three plausible theories were wrong first.

1. *"CI does not sign, local does."* Both bundles reported the identical
   `adhoc,linker-signed` main executable, identifier `tmp1fyd1rtz`, and
   `Sealed Resources=none`. Not the difference.
2. *"Quarantine."* The installed copy carried `com.apple.quarantine` and the
   local one did not. Clearing it with `xattr -cr` and relaunching still gave
   SIGKILL. Not the difference.
3. *"The Word dylib."* It genuinely is unsigned in CI and signed locally, and
   the crash was inside `dlopen`. Ad-hoc signing it and relaunching still gave
   SIGKILL. Real bug, wrong crash.

What actually located it was diffing the two bundles file by file. They had the
same 180 files; five differed by content, and `lipo -archs` on the CI copy of
`ChannelPrefs` printed **nothing** — the file was no longer recognisable as a
Mach-O at all, while the local copy printed `x86_64 arm64`. Ad-hoc signing that
one file made the crashing app launch.

The generalisable lesson is the ordering: hypotheses about *why* two artifacts
behave differently are cheap and mostly wrong; establishing *where* they differ
is mechanical and answered it in one step. The file-by-file diff should have
been the first move, not the fourth.

## Verification

A release-channel build run locally — the path CI takes and the path no local
build had ever taken:

```bash
app/scripts/prepare_build -s build -o "$build_dir" -c release
app/build.sh -d "$build_dir" -p m -c release
```

| Check | Result |
| --- | --- |
| `lipo -archs` ChannelPrefs | `x86_64 arm64` |
| `codesign --verify --strict` ChannelPrefs | passes (`adhoc,runtime`) |
| `codesign --verify --strict` Word dylib | passes (`adhoc,runtime`) |
| Channel string in binary | `release` |
| `CFBundleIdentifier` | `top.selab.pharos` (unsuffixed — the suffix is applied only for `beta`/`dev`/`source`) |
| Mount `.dmg`, copy out, set `com.apple.quarantine`, launch | runs and stays running |

## The audit that followed

Shipping two broken releases in a row is a pattern, not two accidents, so the
whole pipeline was audited from four independent angles — the missing Windows
artifact, other ways a green build can ship a broken binary, the integrity of
the artifacts that did build, and whether the release notes are true — with
every finding handed to a separate agent whose job was to refute it. Eleven
survived; three did not.

### The Windows build has never once shipped

`build.sh:1065` writes `$DIST_DIR/$APP_NAME-${VERSION}_$arch.zip`, and `$arch`
at that point has been through `get_canonical_arch`, which turns `x64` into
`win-x64`. The file is `Pharos-1.0.0_win-x64.zip`. The workflow's upload glob
said `*_x64.zip`.

One character. The Windows job downloaded the Gecko runtime, staged the app and
wrote a correct zip on every tagged release since `desktop-v0.1.0`; the upload
step matched zero files, the job went green, and the runner deleted the
workspace. Five releases, no Windows build, no red job.

It survived because the two names look alike and because nothing checked. The
Linux glob works by accident of a different naming shape — its format string
carries `_linux-` as a literal and `$arch` holds only `x86_64` — so reading one
and inferring the other gives the wrong answer.

`actions/upload-artifact` defaults to `if-no-files-found: warn`. Both upload
steps now set `error`: a build that produces no artifact has not succeeded.

### The schema guard fired after three writes to the shared library

The one that matters most, because it is the user's real library.

`Zotero.Pharos.SharedLibrary.assertMigrationAllowed()` was called immediately
before the migration transaction — the obvious place, and the wrong one. Between
the top of `updateSchema()` and that point, Zotero writes to the database three
times: `backUpDatabase`, `integrityCheck(true)`, and `_fixSciteValues()`, which
issues `UPDATE itemDataValues` and `DELETE FROM itemData` and has its errors
swallowed by `logError`.

So the release notes' promise — "it refuses to open a library whose schema does
not match" — was not what the code did. A mismatched library got written to
first and refused second.

The guard now runs immediately after the compatibility check, before anything
reads or writes. That position is load-bearing, so `pharosSharedLibraryTest.js`
now pins it: `integrityCheckRequired()` is the first call after the guard, and
the test asserts it has not run when the guard throws. Move the guard back down
past anything and the test fails.

The note was corrected too, to say **migrate** rather than **open** — by the
time the guard runs, `Zotero.DB` has already opened `zotero.sqlite` in WAL mode,
so "does not open" is not a promise this code can make.

### Install instructions that cannot work

"The first launch needs Control-click, Open" appeared in seven places. macOS 15
removed that bypass for code with no usable signature; on 15 and later the
override lives only in System Settings → Privacy & Security → Open Anyway, and
it appears only after a launch has already been blocked.

Measured rather than assumed: `spctl --assess` reports `source=no usable
signature`, and Apple's own `syspolicy_check distribution` calls it
`Codesign Error … File is not signed at all` at Fatal severity. The bundle is
not merely un-notarised; it has no `_CodeSignature` directory at all.

### Two more claims in the notes that were false

- **"Pharos opens your existing Zotero library."** Only if that library is at
  `~/Zotero`. Pharos does not read Zotero's settings, so a relocated data
  directory is not found and a new empty one is created silently. Worse, the
  obvious repair — Settings → Data Directory Location — ends at a Zotero dialog
  reading "move files from your existing Zotero data directory to the new
  location", where "existing" now means the empty directory Pharos just made.
  A user obeying it literally copies an empty database over their real one. The
  notes now give `-datadir` as the first answer and warn about that sentence by
  name.
- **"Linux — run `zotero`, for the same reason."** The Windows `zotero.exe` is a
  prebuilt binary checked into the tree; the Linux `zotero` is a bash script.
  Same name, and on Linux genuinely just a rename nobody has done.

### Same bug class, found before it shipped

`fetch_xulrunner` patched `InfoPlist.strings` inside `plugin-container.app` to
rename subprocesses from "FirefoxCP" to "ZoteroCP". That file is a sealed
resource of a bundle Mozilla signs, so editing it breaks the seal — structurally
identical to ChannelPrefs, and equally invisible to a green build.

It is deleted rather than repaired. The ChannelPrefs recipe does not transfer:
`plugin-container` carries hardened runtime and links Mozilla-signed dylibs, so
ad-hoc re-signing strips the team ID and the `allow-jit` entitlement while
leaving library validation on — which kills every content process at exec. What
is lost is a string in Activity Monitor that said "ZoteroCP", which was not
Pharos branding anyway.

Two smaller ones went with it: the DMG's committed `.DS_Store` positioned an icon
named `Zotero.app`, so `Pharos.app` got no saved position and Finder auto-placed
it wherever it liked — fixed by an equal-length UTF-16 substitution of the record
key, both names being exactly ten code units, verified to leave the 6148-byte
B-tree structurally intact. And `LSMinimumSystemVersion` claimed 10.9 while every
Mach-O in the bundle reports `minos 10.15`, so 10.9–10.14 got a dyld crash where
LaunchServices should have given a sentence.

### What was refuted

Three findings did not survive: that the macOS cross-build path ships a
kernel-killed app (the new warning covers it), that a custom mac XUL is copied
in unsigned (it is not), and a duplicate of the plugin-container finding that
proposed the ad-hoc re-sign that would have broken it worse.

Worth recording that the verification pass earned its cost twice over: it did not
just kill three findings, it corrected the proposed fix on four that were real —
including the plugin-container one, where the obvious repair was demonstrated,
by running it on a scratch copy, to turn a cosmetic seal break into a hard crash.

### The ordering lesson, again

The ChannelPrefs hunt cost three wrong hypotheses before a file-by-file diff
answered it in one step. The audit found the Windows bug the same way: not by
reasoning about why the job might not have uploaded, but by asking what filename
the code actually writes and comparing it to the glob. Both times the mechanical
comparison was cheap, decisive, and not the first thing tried.

## The 1.0.1 first-window regression

Version 1.0.1 made the standalone Pharos sign-in window actually become the
application's first window. That fixed the dead login gate from 1.0.0, but it
also broke an assumption inherited from Zotero: core initialization started the
Word and LibreOffice installers as though a library window and its `ZoteroPane`
already existed.

On a fresh macOS profile the result was two unrelated office-integration flows
over the sign-in form. LibreOffice could open its installation wizard, while
the Word permission banner dereferenced a null pane and displayed:

```
TypeError: can't access property "showMacWordPluginInstallWarning", zp is null
```

This was a Pharos startup-order regression, not an upstream Zotero defect. A
normal Zotero launch opens `zoteroPane.xhtml` first, so the implicit pane
precondition happens to hold; Pharos 1.0.1 deliberately opens only
`pharosAuth.xhtml` until the user signs in or chooses local mode.

Version 1.0.2 keeps the office communication services available at startup but
defers their automatic installers whenever no active library pane exists. They
resume after `Zotero.uiReadyPromise`, before which they neither inspect plugin
versions nor record an attempted installation. A forced installation from
Settings remains immediate, and the Mac Word banner has a second null-pane
guard so a future window-order race cannot become a user-facing exception.

The regression test uses a fake installer and a pending UI promise to pin all
three branches: auth-only startup waits without changing attempt state, normal
startup with a pane keeps the upstream immediate path, and manual installation
bypasses the wait. A real fresh-profile launch was also checked on macOS: the
only visible window was `登录 Pharos`, and both installers logged that they were
waiting for the main interface.

## Still open

- **The bundle as a whole is unsigned**, by decision 6 — there is no Apple
  Developer account. `codesign --verify` reports "code object is not signed at
  all", `spctl --assess` reports "no usable signature", and Apple's own
  `syspolicy_check distribution` calls it a fatal pre-distribution error. The
  first launch is therefore refused and has to be allowed once under System
  Settings → Privacy & Security → Open Anyway; Control-click → Open, which every
  doc in this repo used to say, stopped working in macOS 15. Fixing this
  properly is `DEVELOPER_ID` plus the `NOTARIZATION_*` values in
  `app/config-custom.sh` and nothing else.
- **The executable inside the bundle is still named `zotero`**
  (`CFBundleExecutable`). Cosmetic in the Dock, visible in Activity Monitor and
  in crash reports — which is how it was read throughout this investigation.
- **Pharos cannot find a relocated Zotero library.** It reads none of Zotero's
  own settings, so a data directory the user moved — an external drive, Dropbox,
  anywhere — is not found, and a new empty `~/Zotero` is created silently
  instead. The release notes now lead with `-datadir` and warn about the
  misleading repair path, but notes are a workaround, not a fix. The durable one
  is in the client: extend `Zotero.Profile.getOtherAppProfilesDir` — or add a
  sibling — so the "New installation" branch at `dataDirectory.js:183` also reads
  Zotero's own profile root for `extensions.zotero.dataDir`, using the
  `readPrefsFromFile` path already written for Firefox at
  `dataDirectory.js:262-275`. Failing that, prompt on first run rather than
  silently creating a library.
- **The deployed backend lags the code**, missing `/api/auth/status` and
  `/api/evidence`. The desktop degrades rather than fails, but the features
  behind those routes are dark until it is redeployed.

## Verified end state

Run `30882249864`, tag `desktop-v1.0.0`, all four jobs green and — for the first
time — **three** artifacts:

| Artifact | Size |
| --- | --- |
| `pharos-macos` | 179.1 MB |
| `pharos-windows` | 124.9 MB |
| `pharos-linux` | 105.7 MB |

The macOS build was additionally verified locally on the same commit, on the
release channel, by mounting the produced `.dmg`, copying the app out, setting
`com.apple.quarantine` on it, and launching: it runs. `ChannelPrefs` verifies,
the Word dylib verifies, the DMG window positions `Pharos.app`, and the shared
library's ordering guard is pinned by a test proven to fail when the guard moves.
