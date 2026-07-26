#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT=${PHAROS_ROOT:-"$HOME/pharos"}
STATE_DIR="$ROOT/state"
previous_image=$(cat "$STATE_DIR/previous-image" 2>/dev/null || true)
previous_release=$(cat "$STATE_DIR/previous-release" 2>/dev/null || true)
active_image=$(cat "$STATE_DIR/active-image" 2>/dev/null || true)
active_release=$(cat "$STATE_DIR/active-release" 2>/dev/null || true)
COMPOSE="$ROOT/releases/$previous_release/compose.prod.yml"

if [[ -z "$previous_image" || -z "$previous_release" || -z "$active_image" || -z "$active_release" || ! -f "$COMPOSE" ]]; then
  echo "no complete previous Pharos release is available" >&2
  exit 2
fi

exec 9>"$STATE_DIR/deploy.lock"
if ! flock -n 9; then
  echo "another Pharos deployment is already running" >&2
  exit 73
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
python3 "$ROOT/current/server/backup_db.py" \
  "$ROOT/shared/data/pharos.db" \
  "$ROOT/shared/backups/pharos-$timestamp-before-manual-rollback.db"

export PHAROS_IMAGE="$previous_image"
export CLOUDFLARED_IMAGE=${CLOUDFLARED_IMAGE:-cloudflare/cloudflared:latest}
docker compose -p pharos -f "$COMPOSE" up -d --no-build --no-deps --force-recreate api

for _ in $(seq 1 60); do
  if curl --connect-timeout 2 --max-time 5 -fsS http://127.0.0.1:8400/api/health >/dev/null 2>&1; then
    printf '%s\n' "$active_image" > "$STATE_DIR/previous-image"
    printf '%s\n' "$active_release" > "$STATE_DIR/previous-release"
    printf '%s\n' "$previous_image" > "$STATE_DIR/active-image"
    printf '%s\n' "$previous_release" > "$STATE_DIR/active-release"
    ln -sfn "$ROOT/releases/$previous_release" "$ROOT/current"
    echo "rolled back Pharos to $previous_image"
    exit 0
  fi
  sleep 2
done

echo "rollback image did not become healthy; inspect pharos-api logs" >&2
docker logs --tail 80 pharos-api 2>&1 || true
exit 1
