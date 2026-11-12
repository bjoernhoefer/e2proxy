#!/bin/bash
# EMERGENCY PATH ONLY – normal rollouts go through CI (build-deploy.yml -> pi-deploy).
# Builds the image locally on the Pi from a checkout, runs the selftest in a throwaway
# container and only then swaps the running service. The running version stays
# untouched if any step fails.
#   usage: deploy/manual-deploy.sh [compose_dir]     (run from the repo checkout)
set -euo pipefail
COMPOSE_DIR="${1:-$HOME/e2proxy}"
COMPOSE=(docker compose --project-directory "$COMPOSE_DIR" -f "$COMPOSE_DIR/docker-compose.e2proxy.yml")
IMAGE=ghcr.io/bjoernhoefer/e2proxy
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo manual)"

echo "== 1/3 build $IMAGE:manual ($SHA) =="
docker build -q --build-arg GIT_SHA="$SHA" --build-arg GIT_BRANCH=manual -t "$IMAGE:manual" .

echo "== 2/3 selftest (port 18888, temp data) =="
tmp="$(mktemp -d)"
cleanup() { docker rm -f e2proxy-selftest >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
docker run -d --name e2proxy-selftest -p 18888:8888 -v "$tmp:/data" "$IMAGE:manual" >/dev/null
sleep 5
docker exec e2proxy-selftest python3 /app/selftest.py

echo "== 3/3 swap =="
docker tag "$IMAGE:latest" "$IMAGE:previous" 2>/dev/null || true
docker tag "$IMAGE:manual" "$IMAGE:latest"
"${COMPOSE[@]}" up -d
echo "done – rollback: docker tag $IMAGE:previous $IMAGE:latest && ${COMPOSE[*]} up -d"
