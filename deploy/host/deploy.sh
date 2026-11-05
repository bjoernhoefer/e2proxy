#!/usr/bin/env bash
# Rollout of e2proxy on the host (run by the self-hosted GitHub runner).
#
# Gate first, then copy: a broken build never replaces a working one.
#   E2PROXY_DIR   deployment directory on the host (default: $HOME/e2proxy)
#   E2PROXY_URL   base URL for the post-deploy health check (default: http://localhost:8888)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
E2PROXY_DIR="${E2PROXY_DIR:-$HOME/e2proxy}"
E2PROXY_URL="${E2PROXY_URL:-http://localhost:8888}"
COMPOSE="$E2PROXY_DIR/docker-compose.e2proxy.yml"
TS="$(date +%Y%m%d-%H%M%S)"

echo "Source:  $SRC_DIR"
echo "Target:  $E2PROXY_DIR"

SC="$(mktemp -d)"
trap 'rm -rf "$SC"' EXIT
python3 -m py_compile "$SRC_DIR/e2proxy.py"
E2PROXY_DATA_DIR="$SC" python3 "$SRC_DIR/e2proxy.py" --selfcheck

mkdir -p "$E2PROXY_DIR"
changed_py=0; changed_compose=0
cmp -s "$SRC_DIR/e2proxy.py" "$E2PROXY_DIR/e2proxy.py" || changed_py=1
cmp -s "$SRC_DIR/docker-compose.e2proxy.yml" "$COMPOSE" || changed_compose=1

if [ "$changed_py" = 0 ] && [ "$changed_compose" = 0 ]; then
  echo "Nothing changed — deployment is already up to date."
  exit 0
fi

if [ "$changed_py" = 1 ]; then
  [ -f "$E2PROXY_DIR/e2proxy.py" ] && cp "$E2PROXY_DIR/e2proxy.py" "$E2PROXY_DIR/e2proxy.py.bak.$TS"
  cp "$SRC_DIR/e2proxy.py" "$E2PROXY_DIR/e2proxy.py"
  echo "e2proxy.py updated (backup: e2proxy.py.bak.$TS)"
fi
if [ "$changed_compose" = 1 ]; then
  [ -f "$COMPOSE" ] && cp "$COMPOSE" "$COMPOSE.bak.$TS"
  cp "$SRC_DIR/docker-compose.e2proxy.yml" "$COMPOSE"
  echo "docker-compose.e2proxy.yml updated (backup: .bak.$TS)"
fi

cd "$E2PROXY_DIR"
if [ "$changed_compose" = 1 ]; then
  docker compose -f "$COMPOSE" up -d
else
  docker compose -f "$COMPOSE" restart
fi

# Startup installs ffmpeg via apt and loads channels — allow a generous window.
echo "Waiting for $E2PROXY_URL/api/health ..."
for i in $(seq 1 60); do
  if curl -fsS -m 5 "$E2PROXY_URL/api/health" >/dev/null 2>&1; then
    echo "Healthy after ~$((i * 5))s — version: $(curl -fsS -m 5 "$E2PROXY_URL/api/version" || echo '?')"
    exit 0
  fi
  sleep 5
done
echo "::error::e2proxy not healthy 5 min after deploy"
docker logs --tail 80 e2proxy 2>&1 || true
exit 1
