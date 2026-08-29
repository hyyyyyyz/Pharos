#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$ROOT_DIR/vendor/deepseek-harness"
SYNC_SCRIPT="$ROOT_DIR/scripts/vendor-deepseek-harness.sh"
MANIFEST="$ROOT_DIR/vendor/README.md"

fail() {
	echo "DeepSeek Harness vendor check failed: $*" >&2
	exit 1
}

[[ -d "$TARGET_DIR" ]] || fail "missing vendor/deepseek-harness"
[[ -f "$TARGET_DIR/LICENSE" ]] || fail "missing upstream LICENSE"
[[ -f "$TARGET_DIR/THIRD_PARTY_NOTICES.md" ]] || fail "missing upstream notices"
[[ -f "$TARGET_DIR/SAFETY.md" ]] || fail "missing upstream safety disclosure"
[[ -f "$TARGET_DIR/package.json" ]] || fail "missing upstream package manifest"

if find "$TARGET_DIR" -name .git -print -quit | grep -q .; then
	fail "nested .git metadata is present"
fi

script_revision="$(
	sed -n 's/^UPSTREAM_REVISION="\([0-9a-f]\{40\}\)"$/\1/p' "$SYNC_SCRIPT"
)"
[[ -n "$script_revision" ]] || fail "sync script has no 40-character revision pin"
grep -Fq "Upstream revision: \`$script_revision\`" "$MANIFEST" \
	|| fail "vendor manifest and sync script revisions differ"

python3 - "$TARGET_DIR/package.json" "$MANIFEST" <<'PY'
import json
import pathlib
import sys

package_path = pathlib.Path(sys.argv[1])
manifest = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
version = json.loads(package_path.read_text(encoding="utf-8"))["version"]
if f"Upstream version at that revision: `{version}`" not in manifest:
    raise SystemExit("vendor manifest and package.json versions differ")
PY

echo "DeepSeek Harness vendor metadata is consistent ($script_revision)."
