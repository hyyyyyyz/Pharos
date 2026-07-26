#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

IMAGE=${1:?usage: deploy-release.sh IMAGE RELEASE_ID}
RELEASE_ID=${2:?usage: deploy-release.sh IMAGE RELEASE_ID}
ROOT=${PHAROS_ROOT:-"$HOME/pharos"}
RELEASE_DIR="$ROOT/releases/$RELEASE_ID"
COMPOSE="$RELEASE_DIR/compose.prod.yml"
STATE_DIR="$ROOT/state"
STATE_FILE="$STATE_DIR/$RELEASE_ID.status"
SECRETS="$ROOT/shared/secrets/backend.env"
BACKUP_SCRIPT="$RELEASE_DIR/server/backup_db.py"

mkdir -p "$STATE_DIR"
printf 'deploying\n' > "$STATE_FILE"

on_exit() {
  local code=$?
  if [[ "$code" -ne 0 ]]; then
    printf 'failed:%s\n' "$code" > "$STATE_FILE"
  fi
}
trap on_exit EXIT

exec 9>"$STATE_DIR/deploy.lock"
if ! flock -n 9; then
  echo "another Pharos deployment is already running" >&2
  exit 73
fi

if [[ ! -f "$COMPOSE" ]]; then
  echo "missing release compose file: $COMPOSE" >&2
  exit 2
fi
if [[ "$IMAGE" != ghcr.io/hyyyyyyz/pharos:sha-* ]]; then
  echo "refusing mutable or unexpected image: $IMAGE" >&2
  exit 2
fi

docker compose version >/dev/null

protected_new_id=$(docker ps --filter 'name=^/new-api$' --format '{{.ID}}')
protected_proxy_ids=$(docker ps --filter name=cli-proxy-api --format '{{.ID}}' | sort)
protected_proxy_count=$(printf '%s\n' "$protected_proxy_ids" | sed '/^$/d' | wc -l)
protected_api_code=$(curl --connect-timeout 10 --max-time 20 -sS -o /dev/null -w '%{http_code}' https://api.selab.top/api/status)

# The upstream proxy pool may be expanded independently of Pharos. Require the
# known healthy floor, then pin the exact running container set for the duration
# of this deployment so Pharos can neither hide nor cause a lifecycle change.
if [[ -z "$protected_new_id" || "$protected_proxy_count" -lt 11 || "$protected_api_code" != 200 ]]; then
  echo "protected production baseline is unhealthy; refusing to deploy Pharos" >&2
  exit 70
fi

mkdir -p \
  "$ROOT/shared/data" \
  "$ROOT/shared/cache" \
  "$ROOT/shared/tmp" \
  "$ROOT/shared/backups" \
  "$ROOT/shared/secrets" \
  "$ROOT/shared/cloudflared"
chmod 700 "$ROOT/shared/secrets" "$ROOT/shared/cloudflared"

if [[ ! -f "$SECRETS" ]]; then
  auth_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  {
    printf 'PHAROS_AUTH_SECRET=%s\n' "$auth_secret"
    printf 'PHAROS_CORS_ORIGINS=https://hyyyyyyz.github.io,tauri://localhost,http://tauri.localhost\n'
    printf 'PHAROS_ALLOW_REGISTRATION=true\n'
    printf 'PHAROS_TRANSLATOR_TYPE=bing\n'
    printf 'PHAROS_CHAT_PROVIDER=\n'
    printf 'PHAROS_MAX_CONCURRENT_JOBS=1\n'
    printf 'PHAROS_QPS=2\n'
    printf 'PHAROS_DAILY_ENABLED=true\n'
    printf 'PHAROS_DAILY_STARTUP_DELAY=60\n'
  } > "$SECRETS"
fi
chmod 600 "$SECRETS"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
python3 "$BACKUP_SCRIPT" \
  "$ROOT/shared/data/pharos.db" \
  "$ROOT/shared/backups/pharos-$timestamp-before-$RELEASE_ID.db"

# Keep the backup set bounded without touching anything outside Pharos.
mapfile -t old_backups < <(find "$ROOT/shared/backups" -maxdepth 1 -type f -name 'pharos-*.db' -printf '%T@ %p\n' | sort -rn | tail -n +9 | cut -d' ' -f2-)
if (( ${#old_backups[@]} > 0 )); then
  rm -f -- "${old_backups[@]}"
fi

previous_image=$(docker inspect pharos-api --format '{{.Config.Image}}' 2>/dev/null || true)
previous_release=$(cat "$STATE_DIR/active-release" 2>/dev/null || true)
printf '%s\n' "$previous_image" > "$STATE_DIR/previous-image.candidate"

echo "pulling immutable Pharos image $IMAGE"
docker pull "$IMAGE"

export PHAROS_IMAGE="$IMAGE"
docker compose -p pharos -f "$COMPOSE" up -d --no-build --no-deps --force-recreate api

healthy=0
for _ in $(seq 1 90); do
  if curl --connect-timeout 2 --max-time 5 -fsS http://127.0.0.1:8400/api/health \
      | python3 -c 'import json,sys; assert json.load(sys.stdin).get("status") == "ok"' 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ "$healthy" -eq 1 ]]; then
  if ! docker exec pharos-api /opt/pharos-engine/bin/python -c 'import pdf2zh_next' >/dev/null 2>&1; then
    echo "Pharos API is healthy but the isolated translation engine cannot initialize" >&2
    healthy=0
  fi
fi

if [[ "$healthy" -ne 1 ]]; then
  echo "new Pharos release failed health checks" >&2
  docker logs --tail 80 pharos-api 2>&1 || true
  if [[ -n "$previous_image" ]]; then
    echo "rolling back to $previous_image"
    export PHAROS_IMAGE="$previous_image"
    docker compose -p pharos -f "$COMPOSE" up -d --no-build --no-deps --force-recreate api
    for _ in $(seq 1 45); do
      if curl --connect-timeout 2 --max-time 5 -fsS http://127.0.0.1:8400/api/health >/dev/null 2>&1; then
        break
      fi
      sleep 2
    done
  fi
  exit 1
fi

post_new_id=$(docker ps --filter 'name=^/new-api$' --format '{{.ID}}')
post_proxy_ids=$(docker ps --filter name=cli-proxy-api --format '{{.ID}}' | sort)
post_api_code=$(curl --connect-timeout 10 --max-time 20 -sS -o /dev/null -w '%{http_code}' https://api.selab.top/api/status)

if [[ "$post_new_id" != "$protected_new_id" || "$post_proxy_ids" != "$protected_proxy_ids" || "$post_api_code" != 200 ]]; then
  echo "protected production baseline changed during deployment; Pharos was not allowed to touch it" >&2
  exit 71
fi

if [[ -n "$previous_image" && "$previous_image" != "$IMAGE" ]]; then
  printf '%s\n' "$previous_image" > "$STATE_DIR/previous-image"
  if [[ -n "$previous_release" && -d "$ROOT/releases/$previous_release" ]]; then
    printf '%s\n' "$previous_release" > "$STATE_DIR/previous-release"
  fi
fi
printf '%s\n' "$IMAGE" > "$STATE_DIR/active-image"
printf '%s\n' "$RELEASE_ID" > "$STATE_DIR/active-release"
ln -sfn "$RELEASE_DIR" "$ROOT/current"
printf 'success\n' > "$STATE_FILE"

echo "Pharos release $RELEASE_ID is healthy on 127.0.0.1:8400"
docker ps --filter 'name=^/pharos-api$' --format '{{.Names}}|{{.Status}}|{{.Ports}}'
echo "protected:new-api=$post_new_id cli-proxy-count=$protected_proxy_count api-status=$post_api_code"
