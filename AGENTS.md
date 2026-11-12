# AGENTS.md – e2proxy

Binding rules for humans and coding agents. Read `.specify/memory/constitution.md` first
(reliable · slim · fast · durable · modular), then this file.

## Layout
| Path | Concern |
|------|---------|
| `e2proxy/app.py` | start-up wiring (`run`), `--selfcheck`, `main` |
| `e2proxy/http_proxy.py` | `ProxyHandler` – legacy routes; dispatches to `extensions` first |
| `e2proxy/extensions.py` | **open interface**: `@route(method, path, extension)`, `Extension(name, start, enabled)` |
| `e2proxy/receivers.py`, `switching.py`, `streaming.py` | tuner allocation (refcount/epoch), zap tuning, ffmpeg/passthrough |
| `e2proxy/epg.py`, `recordings.py`, `openwebif.py`, `hdhr.py`, … | EPG, recordings, OpenWebif / Plex emulation |
| `e2proxy/recorder/` | optional integrated recorder (ex e2recorder): `engine` (logic, injectable I/O), `matching` (pure), `store`, `routes`, `ui` |
| `e2proxy/webui/` | server-rendered pages + JS self-check |
| `tests/` | pytest suite (unit, regression, HTTP, WebUI JS parse) |
| `selftest.py` | container selftest (CI + pi-deploy) |
| `deploy/` | `targets.json` (rollout hosts), `resolve_targets.py`, emergency `manual-deploy.sh`, Grafana |

## Adding a feature as an extension
```python
from e2proxy import extensions
extensions.register(extensions.Extension(name="foo", start=_start, enabled=lambda: cfg_on()))

@extensions.route("GET", "/api/foo/items", "foo")      # trailing "/" = prefix route
def items(req):                                         # req.method/path/query/data/client_ip, req.arg()
    return {"items": []}                                # dict/list → JSON, str → HTML, (payload, status)
```
Routes of a disabled extension 404; `start` runs once in `app.run` (and again on enable).

## Personas (reviewers) – `.github/agents/`
DVB & streaming specialist · Raspberry Pi enthusiast · Python developer · UI developer ·
Documentation & QA. Every PR states which personas reviewed it. Documentation & QA decides
minor/major, bumps `VERSION` (`e2proxy/version.py`) and adds the changelog entry on the Help page
(`e2proxy/webui/page_help.py` – the project's existing changelog; deliberately no separate
`CHANGELOG.md`).

## Commands
```bash
pip install pytest ruff
ruff check e2proxy tests selftest.py deploy
pytest -q
E2PROXY_DATA_DIR=$(mktemp -d) python -m e2proxy --selfcheck
E2PROXY_DATA_DIR=$(mktemp -d) python -m e2proxy            # dev server on :8888
docker build -t e2proxy:dev . && docker run -d --name e2 e2proxy:dev && docker exec e2 python3 /app/selftest.py
```

<!-- pi-ops-template: agents-deploy v1 · Source: bjoernhoefer/raspberrypi_management templates/app/AGENTS.deploy.md -->
## Deployment (pi-ops standard)

This app is deployed to a Raspberry Pi following the central standard
[`bjoernhoefer/raspberrypi_management` → `docs/standards/app-cicd.md`](https://github.com/bjoernhoefer/raspberrypi_management/blob/main/docs/standards/app-cicd.md).
Read it via `gh api repos/bjoernhoefer/raspberrypi_management/contents/docs/standards/app-cicd.md --jq .content | base64 -d`.

- **App ID / host:** `e2proxy` on `wien-streamer` (entry in `inventory/apps.yml` of raspberrypi_management)
- **Flow:** push to `main` → `test.yml` → arm64 image `ghcr.io/bjoernhoefer/e2proxy` → runner
  `wien-streamer-deploy-e2proxy` → `sudo -n -u pi /usr/local/bin/pi-deploy e2proxy <image@digest> <sha>`
- **Multiple Pis:** the deploy job is a matrix over `deploy/targets.json` (`host`, `runner_label`,
  `deploy_user`, `enabled`). New host = add the app to that host in the inventory, install its deploy
  runner (label `deploy-e2proxy-<host>`), add the entry. Manual runs: `workflow_dispatch` input
  `hosts` = `all` | `none` | `host1,host2`. One failing host does not stop the others.
- **Never** change the per-host steps of the `deploy` job in `build-deploy.yml`, add self-hosted jobs elsewhere,
  SSH into the Pi, or bypass `pi-deploy`. Changes to guardrails, memory budget or selftest command go through a PR to
  `inventory/apps.yml` in raspberrypi_management. Emergency only: `deploy/manual-deploy.sh` on the Pi.
- **Shared guardrails (enforced on the Pi):** RAM (MemAvailable), CPU load, temperature, disk, undervoltage,
  memory budget 512 MB RSS, restarts during observation → otherwise automatic rollback.

### App-specific quality gates ("intelligence" of this app)
| Area | Must hold | Tested by |
|------|-----------|-----------|
| Health | `/api/health` answers < 1.5 s without touching receivers | `tests/test_http.py`, selftest `health`, image HEALTHCHECK |
| WebUI | every page renders; all inline JS parses; no undefined handlers | `tests/test_webui.py` (node), `--selfcheck`, selftest `ui`/`webui-selfcheck` |
| Transcoding | ffmpeg with libx264 + aac works in the image | selftest `transcode` |
| Tuner sharing | a tuner is freed only by its last user; stale epochs ignored | `tests/test_core.py` |
| OpenWebif | `e2length` is `MIN:SS` | `tests/test_core.py` |
| Package | every module imports standalone (no import-order cycles) | `tests/test_package.py` |
| Recorder | optional; API 404 when off; dedupe, skip, keep-last, recovery | `tests/test_recorder.py`, `tests/test_http.py` |
| Rollout targets | `deploy/targets.json` valid, input sanitised | `tests/test_deploy.py` |

### Resource profile
- Idle: ~110 MB RSS · Peak: + ~80–150 MB per transcoding ffmpeg (estimate – ⚠️ OFFEN: measure on the Pi) (passthrough: ~5 MB + ≤ 24 MB jitter buffer per stream)
- Heavy operations: transcoding, EPG refresh, compression of recordings (bounded by: tuner count, scheduled compression window)
