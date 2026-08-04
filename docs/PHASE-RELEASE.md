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

## Still open

- **The bundle as a whole is unsigned**, by decision 6 — there is no Apple
  Developer account. First launch therefore needs Control-click → Open. The
  build says so on the way past. Fixing this properly is `DEVELOPER_ID` plus
  the `NOTARIZATION_*` values in `app/config-custom.sh` and nothing else.
- **The executable inside the bundle is still named `zotero`**
  (`CFBundleExecutable`). Cosmetic in the Dock, visible in Activity Monitor and
  in crash reports — which is how it was read throughout this investigation.
- **The Windows job's fix is unverified.** `build.sh:833` was missing its
  `WIN_NATIVE` guard and called `cygpath` on a runner that has none; the guard
  is restored but no green Windows run has confirmed it.
- **The deployed backend lags the code**, missing `/api/auth/status` and
  `/api/evidence`. The desktop degrades rather than fails, but the features
  behind those routes are dark until it is redeployed.
