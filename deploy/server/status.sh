#!/usr/bin/env bash
set -u

ROOT=${PHAROS_ROOT:-"$HOME/pharos"}
active_release=$(cat "$ROOT/state/active-release" 2>/dev/null || printf 'none')
active_image=$(cat "$ROOT/state/active-image" 2>/dev/null || printf 'none')
local_code=$(curl --connect-timeout 2 --max-time 5 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8400/api/health 2>/dev/null || printf '000')
public_code=$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' https://pharos.selab.top/api/health 2>/dev/null || printf '000')
protected_code=$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' https://api.selab.top/api/status 2>/dev/null || printf '000')
proxy_count=$(docker ps --filter name=cli-proxy-api -q | wc -l)

printf 'release=%s\n' "$active_release"
printf 'image=%s\n' "$active_image"
docker ps --filter 'name=^/pharos-api$' --format 'api={{.Status}} ports={{.Ports}}'
docker ps --filter 'name=^/pharos-cloudflared$' --format 'tunnel={{.Status}}'
docker ps --filter 'name=^/new-api$' --format 'protected-new-api={{.Status}}'
printf 'local-health=%s public-health=%s protected-api=%s cli-proxy-count=%s\n' \
  "$local_code" "$public_code" "$protected_code" "$proxy_count"
