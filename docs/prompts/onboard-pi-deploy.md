# Prompt: switch e2proxy on a Raspberry Pi to automated rollouts (one-time onboarding)

> **Usage:** copy everything between the two lines into a **local** GitHub Copilot instance (Copilot CLI /
> VS Code agent mode) that has SSH access to the Pi, ideally started in a local clone of
> `bjoernhoefer/raspberrypi_management`. The prompt is idempotent: every phase checks first and skips what is
> already done. For a further Pi, replace the values in the "Fixed values" table and add the host to
> `deploy/targets.json` (runner label `deploy-e2proxy-<host>`) beforehand.

---

````markdown
# Task: switch e2proxy on `wien-streamer` to automated, guarded rollouts

You are my careful infrastructure operator. You perform a one-time onboarding of the app **e2proxy** on the
Raspberry Pi **wien-streamer** so that every push to `main` of `bjoernhoefer/e2proxy` is rolled out
automatically by the pi-ops standard (`pi-deploy`: guardrails → pull → start → selftest → observation →
automatic rollback). SSH is allowed **only for this onboarding** (pi-ops constitution, exception "runner
installation"). Work **step by step, slowly and double-safe**. A running, working service is more important
than finishing today.

## Fixed values (verify them – do not trust this table blindly)

| Name | Value | Verify against |
|---|---|---|
| APP | `e2proxy` | `inventory/apps.yml` (raspberrypi_management) |
| HOST | `wien-streamer` | `inventory/hosts.yml` |
| PI | `pi@192.168.88.67` | `ip:` in `inventory/hosts.yml` |
| REPO / OPS | `bjoernhoefer/e2proxy` / `bjoernhoefer/raspberrypi_management` | `gh repo view` |
| DIR / FILE | `/home/pi/e2proxy` / `docker-compose.e2proxy.yml` | `compose_dir` / `compose_file` in `apps.yml` |
| SERVICE / CONTAINER | `e2proxy` / `e2proxy` | compose file |
| IMAGE | `ghcr.io/bjoernhoefer/e2proxy` | `apps.yml`, `.github/workflows/build-deploy.yml` |
| RUNNER LABEL | `deploy-e2proxy` | `deploy/targets.json` in REPO |
| PR | `#20` (v5.0 refactor: image build, new compose file) | `gh pr view 20 -R bjoernhoefer/e2proxy` |
| e2recorder | `/home/pi/e2recorder`, container `e2recorder`, port 8889 | only relevant for phase 9 |

## Safety rules (apply to EVERY step – violating one = stop)

1. **Two gates for every change.** First show the exact commands, the expected result and the rollback command,
   then wait for my explicit "yes". Never batch two changing steps. Read-only checks need no confirmation.
2. **Never delete, never overwrite.** No `rm` (except the throw-away `/tmp/e2proxy-verify*` you create yourself),
   no `docker rm`/`rmi`/`prune` (except your own `e2proxy-verify` container), no `compose down`, no `-v`.
   Rename with the suffix `.pre-v5-$TS` instead (`TS=$(date -u +%Y%m%dT%H%M%SZ)`, determined once, reused).
3. **Rollback first.** Before a change, the rollback command is written down and its prerequisites
   (backup file, old image, old script) are verified to exist.
4. **Verify twice, independently.** Every change is checked by at least two independent means (e.g.
   `docker inspect` *and* HTTP; GitHub API *and* `systemctl` on the Pi).
5. **Stop on surprise.** Any unexpected output, error, warning or deviation from this prompt → stop, report
   what you saw, propose options, wait. Do not improvise fixes on the Pi.
6. **Guardrails before every changing step on the Pi:** `vcgencmd get_throttled` = `throttled=0x0`,
   MemAvailable ≥ 600 MB, load per CPU < 1.5, temperature < 75 °C, ≥ 2 GB free on `/`. Otherwise wait / stop.
7. **Quiet window before every service restart:** no active e2proxy recording (`/api/record/status` →
   empty `recordings`), no active compression (`/api/compression/status`), no e2recorder recording running or
   starting within 30 min, and I confirm that no Plex DVR recording / live-TV session is planned in the next
   30 min.
8. **Secrets:** `data/config.json` and `.env` files may contain credentials. Never print them (`cat`, `head`,
   `diff` on them). List keys only
   (`python3 -c 'import json,sys;print(sorted(json.load(open(sys.argv[1]))))' <file>`).
   Backups `chmod 600`, local copies **outside** every git repository.
9. **Scope:** do not touch any other container (plex, cloudflared, adsb/alloy/otel, node_exporter, …), no
   `apt`, no reboot, no change to `pi-deploy`, to guardrails in `inventory/` or to the deploy steps in
   `build-deploy.yml`. e2recorder only in phase 9.
10. **SSH hygiene:** `ssh -o BatchMode=yes -o ConnectTimeout=10`. One logical command per call. Never accept
    an unknown host key – if `ssh-keygen -F 192.168.88.67` finds nothing, ask me to confirm the fingerprint.
11. **Journal:** keep a local journal (`~/pi-onboarding/e2proxy-$TS.md`): UTC time, command, result,
    decision. It is the source for the final report and the documentation PR (phase 10).

## Phase 0 – local prerequisites (read-only)

- `gh auth status`; `gh api repos/bjoernhoefer/e2proxy --jq .permissions.admin` and the same for OPS → both
  `true` (admin is needed to register the runner).
- OPS clone: `git status` clean, `git pull --ff-only`; read `docs/standards/app-cicd.md`,
  `scripts/install-deploy-runner.sh`, `scripts/host/pi-deploy.sh`.
- SSH: `ssh-keygen -F 192.168.88.67`;
  `ssh … 'hostname; dpkg --print-architecture; sudo -n true && echo SUDO_OK'` → `wien-streamer`, `arm64`, `SUDO_OK`.
- PR: `gh pr view 20 -R bjoernhoefer/e2proxy --json state,mergeable,headRefOid,statusCheckRollup`.
- **Safety net for the merge:**
  `gh api repos/bjoernhoefer/e2proxy/actions/runners --jq '.runners[] | [.name,.status,([.labels[].name]|join(","))]'`
  → there must be **no** runner with label `deploy-e2proxy` yet. Then no deploy job can reach the Pi before
  phase 8. If one exists: stop and report.
- Report a short status table.

## Phase 1 – inventory of the Pi (read-only)

Collect and show as a table:
- Guardrail values (rule 6), `uptime`, `free -m`, `df -h / /mnt/nvme/recordings`, `findmnt /mnt/nvme/recordings`.
- `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'`.
- `docker inspect e2proxy --format '{{.Config.Image}} {{.Image}} {{.State.Health.Status}} {{.RestartCount}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.project.working_dir"}} {{index .Config.Labels "com.docker.compose.project.config_files"}}'`
  → expected: project `e2proxy`, working dir `/home/pi/e2proxy`, config file `…/docker-compose.e2proxy.yml`.
  `pi-deploy` addresses the service exactly like this (`--project-directory DIR -f DIR/FILE`); a different
  project name would create a second container → **stop** if it differs.
- `ls -la /home/pi/e2proxy /home/pi/e2proxy/data`, `du -sh /home/pi/e2proxy/data/* | sort -h | tail`.
- Compare the Pi's compose file with the **old** repo version (`git show 20cbfba:docker-compose.e2proxy.yml` in the
  e2proxy repo). List every local addition on the Pi (extra volumes, env vars, ports). Each one must be
  carried over in phase 6.
- Existing deploy pieces: `ls -l /usr/local/bin/pi-deploy /etc/pi-deploy/ 2>&1`, `id gh-deploy-e2proxy 2>&1`,
  `systemctl list-units --all 'actions.runner.*' --no-pager`. `id -nG pi` must contain `docker`.
- `grep -o 'cgroup[^ ]*' /proc/cmdline` (memory cgroup on/off – informational only).
- e2proxy: `curl -fsS -m 5 localhost:8888/api/health`, `/api/version`, `/api/record/status`,
  `/api/compression/status`, `/api/epg/status`.
- e2recorder: container state, `curl -fsS -m 5 localhost:8889/api/status` (counts only).
- Stop if any guardrail is red or the e2proxy container is not `healthy`.

## Phase 2 – backups (double)

1. On the Pi (after "yes"):
   `sudo tar -C /home/pi -czf /home/pi/backup-e2proxy-pre-v5-$TS.tgz e2proxy e2recorder/data`
   (if `du` showed cache directories > 500 MB, ask me whether to exclude them with `--exclude`), then
   `sudo chmod 600 …`, `sudo sha256sum …`, and `sudo tar -tzf …` must list `e2proxy/data/config.json` and
   `e2proxy/docker-compose.e2proxy.yml`.
2. Protect the old image from any prune: `docker tag "$(docker inspect -f '{{.Image}}' e2proxy)" e2proxy-legacy:pre-v5`.
3. Copy to the local machine: `~/pi-backups/wien-streamer/` (outside any git repo, directory `700`, file `600`),
   compare the sha256 on both sides.
4. Write the backup paths and checksums into the journal.

## Phase 3 – repository safety settings (public repo + self-hosted runner)

Read first, then (after "yes") set only what is missing. Values from section 4 of
`docs/guides/new-app-repo.md` in OPS:
- `gh api repos/bjoernhoefer/e2proxy/actions/permissions/fork-pr-contributor-approval` → `all_external_contributors`.
- `gh api repos/bjoernhoefer/e2proxy/actions/permissions/workflow` → `default_workflow_permissions=read`,
  `can_approve_pull_request_reviews=false`.
- Ruleset `pi-ops-main` (deletion + non_fast_forward on the default branch): `gh api repos/bjoernhoefer/e2proxy/rulesets`.
- Ask me to confirm that the GitHub App **pi-ops** has access to `bjoernhoefer/e2proxy` (UI only:
  Settings → Applications → pi-ops → Configure).

## Phase 4 – merge PR #20 and publish the image (no Pi involved)

1. Preconditions: PR checks green, phase 0 showed **no** `deploy-e2proxy` runner. Show both, ask for "yes" and
   for the merge method (default `--merge`). Then `gh pr merge 20 -R bjoernhoefer/e2proxy --merge`.
2. Watch the `build-deploy` run on `main`
   (`gh run list -R bjoernhoefer/e2proxy -w build-deploy.yml -b main -L 1`, then `gh run watch <id>`).
   Expected: `test` ✓, image pushed, step **"Verify the image is pullable anonymously" fails** (new GHCR packages
   are private) → `deploy` skipped. If a `deploy` job is ever *queued*: `gh run cancel <id>` at once and verify
   `cancelled`. It cannot run anyway (no runner), but nothing may stay pending.
3. Ask me to make the package public (manual step, GitHub has no API for it):
   `https://github.com/users/bjoernhoefer/packages/container/e2proxy/settings` → Danger Zone → Change
   visibility → Public. Wait for my "done".
4. Verify anonymously (no gh token!):
   `t=$(curl -sS 'https://ghcr.io/token?scope=repository:bjoernhoefer/e2proxy:pull' | jq -r .token)`, then
   `curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $t" -H 'Accept: application/vnd.oci.image.index.v1+json' https://ghcr.io/v2/bjoernhoefer/e2proxy/manifests/main`
   → `200`.
5. A green build **without** deployment:
   `gh workflow run build-deploy.yml -R bjoernhoefer/e2proxy --ref main -f hosts=none` → watch it; all jobs green,
   `deploy` skipped (empty matrix).
6. With the same anonymous token, determine `DIGEST` of tag `sha-<MAIN_SHA>` (full 40-char sha of `main`) via
   `curl -sSI` (`Docker-Content-Digest` header), and check that the index lists `linux/arm64`
   (`GET …/manifests/sha-<MAIN_SHA>` → `.manifests[].platform`). Journal: MAIN_SHA, DIGEST.

## Phase 5 – test the image on the Pi without touching production

1. `docker pull ghcr.io/bjoernhoefer/e2proxy@$DIGEST` as user `pi`, without `docker login` (proves registry access).
2. Empty data: `mkdir -p /tmp/e2proxy-verify-empty`, then
   `docker run -d --name e2proxy-verify --network none --memory 512m -v /tmp/e2proxy-verify-empty:/data ghcr.io/bjoernhoefer/e2proxy@$DIGEST`
   → `docker exec e2proxy-verify python3 /app/selftest.py` must print `OK` for health, ui, webui-selfcheck and
   transcode. Note RSS from `docker stats --no-stream`. Then `docker rm -f e2proxy-verify`.
3. Real configuration, isolated: `sudo cp -a /home/pi/e2proxy/data /tmp/e2proxy-verify-data` and the same
   `docker run` with `-v /tmp/e2proxy-verify-data:/data` and `--network none`. Without a network it cannot reach
   or zap a receiver, cannot announce itself via SSDP and has no recordings mount. Run the selftest, then
   `docker exec e2proxy-verify python3 -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:8888/api/epg/status').read()[:300])"`
   and `docker logs e2proxy-verify 2>&1 | grep -c Traceback` → `0`. Afterwards `docker rm -f e2proxy-verify` and
   `sudo rm -rf /tmp/e2proxy-verify-empty /tmp/e2proxy-verify-data` (the copy contains secrets – it must go).
4. Any failure → stop. Production is still untouched.

## Phase 6 – switch production to the image (the critical step, done once)

`pi-deploy` deliberately never changes the compose file, and its rollback re-tags the *previous* image.
So the switch from the old file-mount setup (`python:3.11-slim` + mounted `e2proxy.py`) must happen here,
before the first `pi-deploy` run.

1. Re-check guardrails (rule 6) and the quiet window (rule 7, ask me about Plex).
2. Upload the new compose file **next to** the old one: `scp` `docker-compose.e2proxy.yml` from the e2proxy `main`
   checkout to `/home/pi/e2proxy/docker-compose.e2proxy.yml.new`. Carry over the local additions found in
   phase 1 (show the diff, ask). Validate:
   `docker compose --project-directory /home/pi/e2proxy -f /home/pi/e2proxy/docker-compose.e2proxy.yml.new config -q`.
3. `docker tag ghcr.io/bjoernhoefer/e2proxy@$DIGEST ghcr.io/bjoernhoefer/e2proxy:latest` (the new compose file
   references `:latest`; `--pull never` below guarantees exactly this image is used).
4. Write down and print the rollback. Prerequisites: the `.pre-v5-$TS` file exists, `e2proxy.py` and `e2probe.py`
   are still in `/home/pi/e2proxy`, image `python:3.11-slim` is present, internet works (the old setup runs
   `apt-get` at start):
   `cp -p docker-compose.e2proxy.yml.pre-v5-$TS docker-compose.e2proxy.yml && docker compose --project-directory /home/pi/e2proxy -f /home/pi/e2proxy/docker-compose.e2proxy.yml up -d --wait --wait-timeout 300 e2proxy`.
5. After "yes", in `/home/pi/e2proxy`:
   `cp -p docker-compose.e2proxy.yml docker-compose.e2proxy.yml.pre-v5-$TS` →
   `mv docker-compose.e2proxy.yml.new docker-compose.e2proxy.yml` →
   `docker compose --project-directory /home/pi/e2proxy -f /home/pi/e2proxy/docker-compose.e2proxy.yml up -d --no-build --pull never --wait --wait-timeout 180 e2proxy`.
6. Verify (all must hold):
   - `docker inspect e2proxy` → image ID equals `docker image inspect ghcr.io/bjoernhoefer/e2proxy@$DIGEST -f '{{.Id}}'`,
     health `healthy`, compose project still `e2proxy`, exactly **one** e2proxy container
     (`docker ps -a --filter name=e2proxy`).
   - `curl -fsS localhost:8888/api/health`; `/api/version` → build id contains the first 7 chars of MAIN_SHA.
   - `docker exec e2proxy python3 /app/selftest.py` → all `OK`.
   - `docker logs --since 10m e2proxy 2>&1 | grep -c Traceback` → `0`; `/api/epg/status` still has data.
   - HDHomeRun emulation for Plex: `curl -fsS localhost:8888/discover.json` and `/lineup.json` answer.
   - Ask me to open `http://192.168.88.67:8888` and to tune one live channel in Plex.
   - Observe 5 minutes: health stays `healthy`, `RestartCount` unchanged, RSS < 512 MB.
7. Anything fails → run the rollback from step 4 immediately, verify the old container is healthy, stop.
8. Leave `e2proxy.py`, `e2probe.py`, `reload_e2proxy.sh` and the `.pre-v5` file in place (cleanup ≥ 14 days
   later, as a separate task).

## Phase 7 – install the deploy runner (pi-ops script)

1. In the OPS clone, create branch `onboard/e2proxy-deploy`. Run `scripts/install-deploy-runner.sh e2proxy --check`
   (no SSH, no changes) and verify the output: `IMAGE_REPO=ghcr.io/bjoernhoefer/e2proxy`,
   `COMPOSE_DIR=/home/pi/e2proxy`, `COMPOSE_FILE=docker-compose.e2proxy.yml`, `SERVICE=e2proxy`,
   `MEM_LIMIT_MB=512`, `SELFTEST=python3 /app/selftest.py`, labels `deploy-e2proxy,wien-streamer`, runner user
   `gh-deploy-e2proxy` without docker group, sudoers line limited to `/usr/local/bin/pi-deploy e2proxy *`.
   Cross-check the label with `deploy/targets.json` in the e2proxy repo.
2. After "yes": `scripts/install-deploy-runner.sh e2proxy`. Stop on any `WARN` or `ERROR`.
3. Verify (GitHub *and* Pi):
   - `gh api repos/bjoernhoefer/e2proxy/actions/runners` → `wien-streamer-deploy-e2proxy`, `online`, labels
     include `deploy-e2proxy`.
   - Pi: `systemctl is-active` of the new `actions.runner.*e2proxy*` unit;
     `ls -l /usr/local/bin/pi-deploy /etc/pi-deploy/e2proxy.conf` → `root:root`, not group/world-writable;
     `sudo cat /etc/pi-deploy/e2proxy.conf` (contains no secrets); `sudo -l -U gh-deploy-e2proxy` → only the
     pi-deploy line.
   - Negative tests (must FAIL): `sudo -u gh-deploy-e2proxy docker ps` → permission denied;
     `sudo -u gh-deploy-e2proxy sudo -n -u pi /usr/local/bin/pi-deploy e2proxy invalid 0000000000000000000000000000000000000000`
     → exit 2 with "image must be …" (proves sudoers and config parsing without deploying anything).

## Phase 8 – first rollout through the pipeline

1. Guardrails + quiet window again, then after "yes":
   `gh workflow run build-deploy.yml -R bjoernhoefer/e2proxy --ref main -f hosts=wien-streamer`; `gh run watch`.
2. Verify: job `deploy (wien-streamer)` green and its log shows `deployment succeeded`; on the Pi
   `journalctl -t pi-deploy --since "30 min ago" --no-pager`; the container runs the digest of this run
   (`docker inspect`), `healthy`, `/api/version` = MAIN_SHA, selftest `OK`, RestartCount 0.
3. If the job fails: `pi-deploy` has rolled back to the image from phase 6. Verify it is running and healthy,
   collect the job log and the journal lines, stop.
4. From now on every merge to `main` deploys automatically.

## Phase 9 – optional: move e2recorder into e2proxy (separate "yes" required)

Only if I want it now. The integrated recorder is off by default; enabling it while e2recorder still runs
would record everything twice.
1. e2recorder quiet: no recording in state `recording`, none `scheduled` within 30 min (show the next 3 –
   titles and times only).
2. Target must be empty: `ls /home/pi/e2proxy/data/recorder/ 2>&1` → no `series.json` / `recordings.json`,
   otherwise stop.
3. Rollback written down: disable the recorder via the settings API, then `docker start e2recorder`.
4. After "yes": `docker stop e2recorder` (stop only – `unless-stopped` keeps it stopped across reboots) →
   `sudo install -d /home/pi/e2proxy/data/recorder` →
   `sudo cp -p /home/pi/e2recorder/data/series.json /home/pi/e2recorder/data/recordings.json /home/pi/e2proxy/data/recorder/`
   → validate both copies with `python3 -m json.tool <file> >/dev/null`.
5. Enable: `GET localhost:8888/api/recorder/settings`, change only `enabled` to `true`, `POST` the object back to
   `/api/recorder/settings` – or I toggle it under Settings → Recorder (ask which).
6. Verify: `/api/recorder/status` → `enabled: true`, `series` = number of entries in `series.json`, recording
   counts plausible against the source file; `POST /api/recorder/scan`, then upcoming episodes are `scheduled`;
   ask me to check the marks in the EPG overview.
7. Keep the e2recorder container stopped (not removed) for at least 14 days.

## Phase 10 – documentation (PR in raspberrypi_management, do not merge it yourself)

On branch `onboard/e2proxy-deploy`:
- `inventory/apps.yml`: `e2proxy` → `status: active` (e2recorder → `retired` only if phase 9 was done and I agree).
- `hosts/wien-streamer/README.md`: e2proxy row (image `ghcr.io/bjoernhoefer/e2proxy`, CI/CD active), runner
  `wien-streamer-deploy-e2proxy` and `pi-deploy` installed, e2recorder state.
- `hosts/wien-streamer/CHANGELOG.md`: dated entry (ISO 8601) – what, why, backups (paths, never contents).
  **Do not edit `operations-log.md`** – only the `pi-task` workflow writes it.
- Run the repo's local checks where possible (`scripts/apps-conformance.sh`, `scripts/stacks-validate.sh`), open the
  PR, link e2proxy PR #20 and the deploy run.

## Final report

A table per phase (✅ / ⏭️ / ❌ + evidence), MAIN_SHA, DIGEST, backup locations and checksums, the exact rollback
commands for today's state, measured RSS (idle and after the selftest), open follow-ups (legacy file cleanup
after ≥ 14 days, removing old backups and the `e2proxy-legacy:pre-v5` tag after a good week, e2recorder retirement).
````

---
