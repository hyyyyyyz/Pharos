#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT=${PHAROS_ROOT:-"$HOME/pharos"}
CF_DIR="$ROOT/shared/cloudflared"
TUNNEL_NAME=${PHAROS_TUNNEL_NAME:-pharos-prod}
HOSTNAME=${PHAROS_PUBLIC_HOSTNAME:-pharos-api.selab.top}
export PHAROS_TUNNEL_NAME="$TUNNEL_NAME"
CF_IMAGE=$(cat "$CF_DIR/image" 2>/dev/null || true)
ACTIVE_IMAGE=$(cat "$ROOT/state/active-image" 2>/dev/null || true)
ACTIVE_RELEASE=$(cat "$ROOT/state/active-release" 2>/dev/null || true)
COMPOSE="$ROOT/releases/$ACTIVE_RELEASE/compose.prod.yml"

if [[ -z "$CF_IMAGE" || ! -f "$CF_DIR/cert.pem" ]]; then
  echo "run tunnel-login first and complete the Cloudflare browser authorization" >&2
  exit 2
fi
if [[ -z "$ACTIVE_IMAGE" || ! -f "$COMPOSE" ]]; then
  echo "deploy a healthy Pharos API before creating its public tunnel" >&2
  exit 2
fi

cf() {
  docker run --rm \
    --name pharos-cloudflared-admin \
    --user 1000:1000 \
    -e HOME=/tmp \
    -w /tmp/.cloudflared \
    -v "$CF_DIR:/tmp/.cloudflared" \
    "$CF_IMAGE" "$@"
}

tunnel_json=$(cf tunnel list --output json)
tunnel_id=$(printf '%s' "$tunnel_json" | python3 -c \
  'import json,os,sys; name=os.environ["PHAROS_TUNNEL_NAME"]; rows=json.load(sys.stdin); print(next((r["id"] for r in rows if r.get("name")==name), ""))')

if [[ -z "$tunnel_id" ]]; then
  cf tunnel create "$TUNNEL_NAME"
  tunnel_json=$(cf tunnel list --output json)
  tunnel_id=$(printf '%s' "$tunnel_json" | python3 -c \
    'import json,os,sys; name=os.environ["PHAROS_TUNNEL_NAME"]; rows=json.load(sys.stdin); print(next((r["id"] for r in rows if r.get("name")==name), ""))')
fi

if [[ -z "$tunnel_id" || ! -f "$CF_DIR/$tunnel_id.json" ]]; then
  echo "tunnel was created but its credential file was not found" >&2
  exit 1
fi

cf tunnel route dns --overwrite-dns "$tunnel_id" "$HOSTNAME"

{
  printf 'tunnel: %s\n' "$tunnel_id"
  printf 'credentials-file: /etc/cloudflared/%s.json\n\n' "$tunnel_id"
  printf 'ingress:\n'
  printf '  - hostname: %s\n' "$HOSTNAME"
  printf '    service: http://pharos-api:8400\n'
  printf '  - service: http_status:404\n'
} > "$CF_DIR/config.yml"
chmod 600 "$CF_DIR/config.yml" "$CF_DIR/$tunnel_id.json" "$CF_DIR/cert.pem"

export PHAROS_IMAGE="$ACTIVE_IMAGE"
export CLOUDFLARED_IMAGE="$CF_IMAGE"
docker compose -p pharos -f "$COMPOSE" --profile tunnel up -d --no-build --no-deps tunnel

for _ in $(seq 1 45); do
  code=$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' "https://$HOSTNAME/api/health" || true)
  if [[ "$code" == 200 ]]; then
    printf '%s\n' "$tunnel_id" > "$CF_DIR/tunnel-id"
    printf '%s\n' "$HOSTNAME" > "$CF_DIR/hostname"
    echo "tunnel=$TUNNEL_NAME id=$tunnel_id hostname=$HOSTNAME https=200"
    exit 0
  fi
  sleep 4
done

docker logs --tail 80 pharos-cloudflared 2>&1 || true
echo "Tunnel started but HTTPS health did not reach 200" >&2
exit 1
