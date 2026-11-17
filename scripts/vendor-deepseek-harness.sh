#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_URL="https://github.com/deepseek-ai/deepseek-harness.git"
UPSTREAM_REVISION="cd5ef8148158c3a752a658978873241fdf8e2bbc"
TARGET_DIR="$ROOT_DIR/vendor/deepseek-harness"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/pharos-deepseek-harness.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

if [[ -n "${DEEPSEEK_HARNESS_SOURCE_DIR:-}" ]]; then
	source_dir="$(cd "$DEEPSEEK_HARNESS_SOURCE_DIR" && pwd)"
else
	source_dir="$work_dir/upstream"
	# Fetch the immutable object directly instead of cloning the moving default
	# branch.  A normal --depth 1 clone stops being reproducible as soon as
	# upstream advances beyond the revision pinned above.
	git init --quiet "$source_dir"
	git -C "$source_dir" remote add origin "$UPSTREAM_URL"
	git -C "$source_dir" fetch --quiet --depth 1 --no-tags origin "$UPSTREAM_REVISION"
	git -C "$source_dir" checkout --quiet --detach FETCH_HEAD
fi

if [[ ! -d "$source_dir/.git" ]]; then
	echo "DeepSeek Harness source must be a Git checkout so its revision can be verified." >&2
	exit 1
fi

actual_revision="$(git -C "$source_dir" rev-parse HEAD)"
if [[ "$actual_revision" != "$UPSTREAM_REVISION" ]]; then
	echo "Expected DeepSeek Harness $UPSTREAM_REVISION, got $actual_revision." >&2
	exit 1
fi

staging_dir="$work_dir/staging"
cp -a "$source_dir" "$staging_dir"
rm -rf "$staging_dir/.git"

if find "$staging_dir" -name .git -print -quit | grep -q .; then
	echo "Refusing to vendor a nested .git directory." >&2
	exit 1
fi

mkdir -p "$TARGET_DIR"
rsync -a --delete "$staging_dir/" "$TARGET_DIR/"

echo "Vendored DeepSeek Harness $UPSTREAM_REVISION into $TARGET_DIR"
