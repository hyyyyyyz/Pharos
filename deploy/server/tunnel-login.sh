#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT=${PHAROS_ROOT:-"$HOME/pharos"}
CF_DIR="$ROOT/shared/cloudflared"
mkdir -p "$CF_DIR"
chmod 700 "$CF_DIR"

# Pull only the official cloudflared image. Runtime uses the immutable digest
# recorded below; "latest" is never left in the Compose configuration.
docker pull cloudflare/cloudflared:latest
CF_IMAGE=$(docker image inspect cloudflare/cloudflared:latest --format '{{index .RepoDigests 0}}')
if [[ -z "$CF_IMAGE" ]]; then
  echo "could not resolve cloudflared image digest" >&2
  exit 1
fi
printf '%s\n' "$CF_IMAGE" > "$CF_DIR/image"

exec docker run --rm -it \
  --name pharos-cloudflared-login \
  --user 1000:1000 \
  -e HOME=/tmp \
  -w /tmp/.cloudflared \
  -v "$CF_DIR:/tmp/.cloudflared" \
  "$CF_IMAGE" tunnel login
