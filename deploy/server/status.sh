#!/usr/bin/env bash
set -u

ROOT=${PHAROS_ROOT:-"$HOME/pharos"}
active_release=$(cat "$ROOT/state/active-release" 2>/dev/null || printf 'none')
active_image=$(cat "$ROOT/state/active-image" 2>/dev/null || printf 'none')
local_web_code=$(curl --connect-timeout 2 --max-time 5 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8400/ 2>/dev/null || printf '000')
local_api_code=$(curl --connect-timeout 2 --max-time 5 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8400/api/health 2>/dev/null || printf '000')
public_web_code=$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' https://pharos.selab.top/ 2>/dev/null || printf '000')
public_api_code=$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' https://pharos.selab.top/api/health 2>/dev/null || printf '000')
protected_code=$(curl --connect-timeout 5 --max-time 15 -sS -o /dev/null -w '%{http_code}' https://api.selab.top/api/status 2>/dev/null || printf '000')
proxy_count=$(docker ps --filter name=cli-proxy-api -q | wc -l)
if systemctl is-active --quiet cloudflared 2>/dev/null; then
  tunnel_state=active
else
  tunnel_state=inactive
fi

printf 'release=%s\n' "$active_release"
printf 'image=%s\n' "$active_image"
docker ps --filter 'name=^/pharos-api$' --format 'api={{.Status}} ports={{.Ports}}'
printf 'tunnel=%s\n' "$tunnel_state"
docker ps --filter 'name=^/new-api$' --format 'protected-new-api={{.Status}}'
printf 'local-web=%s local-api=%s public-web=%s public-api=%s protected-api=%s cli-proxy-count=%s\n' \
  "$local_web_code" "$local_api_code" "$public_web_code" "$public_api_code" \
  "$protected_code" "$proxy_count"
