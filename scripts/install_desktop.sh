#!/usr/bin/env bash
#
# Pharos -- build the desktop client from source and install it (macOS).
# -------------------------------------------------------------------
# One command: check prerequisites -> npm ci -> npm run build -> package
# Pharos.app (app/scripts/dir_build -p m) -> copy it to the install location.
#
#   scripts/install_desktop.sh                    # build + install to /Applications
#   scripts/install_desktop.sh --dest ~/Applications
#   scripts/install_desktop.sh --skip-build       # reuse an existing staged build
#   scripts/install_desktop.sh --build-only       # build, do not install
#   scripts/install_desktop.sh --yes              # replace an existing app without asking
#
# The result is a SOURCE build (version "x.y.z.SOURCE.<hash>"): it is ad-hoc
# signed, not notarized, and honours the hidden `pharos.baseURL` pref so it can
# point at a self-hosted backend. See README "Build and install from source".
#
# This script never launches the app and never touches ~/Zotero or ~/Pharos.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLIENT="$ROOT/client"
STAGED="$CLIENT/app/staging/Pharos.app"

DEST_DIR="/Applications"
SKIP_BUILD=0
BUILD_ONLY=0
ASSUME_YES=0
BACKUP=1
THIN=0

log()  { printf '\n\033[1;35m[pharos-install]\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m[pharos-install] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
	sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
	cat <<'EOF'

Options
  --dest DIR      install into DIR (default: /Applications)
  --skip-build    do not build; install client/app/staging/Pharos.app as it is
  --build-only    build the app but do not install it
  --yes, -y       replace an existing Pharos.app without asking
  --host-arch-only  strip the other CPU architecture from the installed copy
                  (~170 MB smaller; the app then runs only on this kind of Mac)
  --no-backup     when replacing, delete the old app instead of moving it aside
  -h, --help      this text
EOF
}

while [ $# -gt 0 ]; do
	case "$1" in
		--dest)       [ $# -ge 2 ] || die "--dest needs a directory"; DEST_DIR="$2"; shift 2 ;;
		--skip-build) SKIP_BUILD=1; shift ;;
		--build-only) BUILD_ONLY=1; shift ;;
		--yes|-y)     ASSUME_YES=1; shift ;;
		--host-arch-only) THIN=1; shift ;;
		--no-backup)  BACKUP=0; shift ;;
		-h|--help)    usage; exit 0 ;;
		*)            usage >&2; die "unknown option: $1" ;;
	esac
done

[ "$(uname -s)" = "Darwin" ] || die "this script supports macOS only. For Linux and Windows see README 'Build and install from source' (not verified)."

# --- prerequisites -----------------------------------------------------------
if [ "$SKIP_BUILD" -eq 0 ]; then
	log "Checking prerequisites ..."
	missing=()
	for tool in node npm git perl python3 curl wget zip unzip xz rsync xxd; do
		command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
	done
	[ -x /usr/bin/codesign ] || missing+=("codesign (install Xcode Command Line Tools: xcode-select --install)")
	if [ ${#missing[@]} -gt 0 ]; then
		printf '  missing: %s\n' "${missing[@]}" >&2
		die "install the tools above first (Homebrew: brew install node wget xz)."
	fi
	node_major="$(node -p 'process.versions.node.split(".")[0]')"
	[ "$node_major" -ge 20 ] || die "Node 20 or newer is required (found $(node -v))."
	# The packaging inputs (app/mac/updater.tar.xz etc.) are plain files in this
	# repository; unlike upstream Zotero it does not use Git LFS.
	[ -s "$CLIENT/app/mac/updater.tar.xz" ] || die "client/app/mac/updater.tar.xz is missing: is this a complete checkout?"
	free_gb="$(df -g "$CLIENT" | awk 'NR==2{print $4}')"
	[ "${free_gb:-0}" -ge 5 ] || die "at least 5 GB of free disk is needed (found ${free_gb}G)."
	echo "  ok: node $(node -v), $(df -h "$CLIENT" | awk 'NR==2{print $4}') free"

	# --- build -----------------------------------------------------------------
	cd "$CLIENT"
	log "Installing JS dependencies (npm ci) ..."
	npm ci
	log "Compiling JS/JSX and SCSS (npm run build) ..."
	npm run build
	log "Packaging Pharos.app (first run downloads Firefox ESR 140 from mozilla.org, ~150 MB) ..."
	app/scripts/dir_build -p m
fi

[ -x "$STAGED/Contents/MacOS/zotero" ] || die "no staged build at $STAGED -- run without --skip-build."

if [ "$BUILD_ONLY" -eq 1 ]; then
	log "Built: $STAGED ($(du -sh "$STAGED" | cut -f1))"
	exit 0
fi

# --- install -----------------------------------------------------------------
TARGET="$DEST_DIR/Pharos.app"
mkdir -p "$DEST_DIR" 2>/dev/null || true
[ -w "$DEST_DIR" ] || die "$DEST_DIR is not writable. Pick another place with --dest, or run with sudo."

if pgrep -f "$TARGET/Contents/MacOS/zotero" >/dev/null 2>&1; then
	die "$TARGET is running. Quit Pharos first."
fi

if [ -e "$TARGET" ]; then
	if [ "$ASSUME_YES" -ne 1 ]; then
		[ -t 0 ] || die "$TARGET already exists; re-run with --yes to replace it."
		printf 'Replace existing %s? [y/N] ' "$TARGET"
		read -r answer
		case "$answer" in y|Y|yes|YES) ;; *) die "aborted; nothing changed." ;; esac
	fi
	if [ "$BACKUP" -eq 1 ]; then
		OLD="$DEST_DIR/Pharos.app.bak-$(date +%Y%m%d%H%M%S)"
		log "Moving the existing app to $OLD"
		mv "$TARGET" "$OLD"
	else
		log "Removing the existing app"
		rm -rf "$TARGET"
	fi
fi

log "Installing to $TARGET ..."
# ditto keeps symlinks, resource forks and the ad-hoc signature intact.
ditto "$STAGED" "$TARGET"

if [ "$THIN" -eq 1 ]; then
	host_arch="$(uname -m)"   # arm64 or x86_64
	log "Stripping everything but $host_arch from the installed copy ..."
	find "$TARGET" -type f | while read -r f; do
		if lipo -archs "$f" 2>/dev/null | grep -q ' '; then
			lipo -thin "$host_arch" "$f" -output "$f.thin" && mv "$f.thin" "$f"
		fi
	done
fi

# The staged app is only partly signed (Mozilla's signatures broke when the
# bundle was modified), and an Apple Silicon Mac is strict about that. Give the
# whole bundle one consistent ad-hoc signature.
log "Ad-hoc signing ..."
codesign --force --deep -s - "$TARGET" 2>&1 | grep -v "replacing existing signature" || true
codesign --verify --deep --strict "$TARGET" \
	&& echo "  signature: ad-hoc, valid" \
	|| die "codesign verification failed for $TARGET"

# Read the schema this build carries rather than naming a number here: the
# baseline has moved before, and a stale figure in this message tells the user
# the wrong thing about which Zotero library the app will agree to open.
SCHEMA="$(head -n 1 "$CLIENT/resource/schema/userdata.sql" | tr -dc '0-9')"

cat <<EOF

Installed: $TARGET ($(du -sh "$TARGET" | cut -f1))

First launch
  - A locally built app carries no download quarantine flag, so macOS normally
    opens it with a plain double-click. If Gatekeeper still objects, run:
        xattr -dr com.apple.quarantine "$TARGET"
  - This is a source build: automatic updates do not apply; rebuild to upgrade.
  - It opens the Zotero library (~/Zotero) only if that library's userdata
    schema is exactly ${SCHEMA:-the one this build carries}. To try it on a throwaway library first, launch with a separate data directory:
        "$TARGET/Contents/MacOS/zotero" -datadir "\$HOME/PharosTry"
  - Self-hosted backend: see README, 'Pointing a source build at your backend'.
EOF
