# External Transcoding — Handoff & Continuation Guide

> Cross-device handoff for the `feature/external_rendering` work (branch
> `agents/external-transcoding-automation`). Read this first when resuming on
> another machine. It captures **what exists, why, how to verify, and what's
> left** so the research isn't lost.

## 1. What this feature is

Offload `.ts → .mkv` ffmpeg transcoding from the Raspberry Pi (e2proxy) to an
**external worker** — an on-demand Azure VM **or** a home/office PC. Transport is
**pluggable**: `azure` (Blob + Storage Queue) or `filestore` (SMB/local). The
worker pulls jobs from a queue, transcodes, uploads the result, and notifies
e2proxy (optional webhook); e2proxy downloads, verifies (sha256), and cleans up
the big local `.ts`.

Design goal: **not cloud-only**. The `filestore` provider makes it work over a
plain SMB share and also makes everything testable without any cloud.

## 2. Current status (all green)

| Area | State |
|------|-------|
| e2proxy module (`e2proxy.py`) | ✅ done — transport providers, config, submit/scheduler, webhook, completion handler, status API, run()-wiring, local-compression gate |
| Worker (`workers/external_transcode_worker.py`) | ✅ done — filestore + azure (connection-string / SAS / **managed identity**) |
| Tests | ✅ 8/8 green — `tests/test_external_transcode_e2e.py` (filestore E2E + fake ffmpeg), `tests/test_azure_rest_transport.py` (Azure REST vs mock server) |
| Docs | ✅ `README.md` section, `workers/README.md` |
| **Azure deploy** | ✅ `deploy/azuredeploy.json` (ARM) + `azuredeploy.parameters.json` + `deploy/README.md` + `deploy/cloud-init.yaml` + `deploy/gen_arm.py` (generator) |

## 3. How to verify after a fresh clone

```bash
# from repo root
python3 tests/test_external_transcode_e2e.py     # 4 tests
python3 tests/test_azure_rest_transport.py        # 4 tests
python3 -c "import py_compile; py_compile.compile('e2proxy.py', doraise=True)"

# ARM template parses
python3 -m json.tool deploy/azuredeploy.json > /dev/null && echo OK
```

The ARM template is **generated** by `deploy/gen_arm.py`, not hand-edited. The
generator assembles the embedded cloud-init as an ARM `concat()` expression and
validates the reconstructed cloud-init YAML with PyYAML:

```bash
python3 -m pip install pyyaml      # only needed to run the generator
python3 deploy/gen_arm.py          # rewrites deploy/azuredeploy.json
```

## 4. Architecture (the "why")

- **e2proxy stays single-file + stdlib-only.** So it talks to Azure via
  **REST + SAS** using `urllib` — no SDK, no new dependencies. e2proxy only does
  Blob PUT/GET/DELETE/HEAD and Queue **send** (never receive).
- **Worker is a separate program** and *does* use the Azure SDK (robust queue
  receive/lease/heartbeat/delete).
- **Webhook is optional.** The scheduler also polls `done.json` in the
  blob/filestore as source-of-truth, so a missed notification never loses a job.
- **Stuck detection:** worker side = queue visibility timeout (lease); e2proxy
  side = `stuck_minutes` timeout → re-enqueue up to `max_attempts`.
- **Integrity:** worker writes sha256 in `done.json`; e2proxy verifies size +
  sha256 before placing the `.mkv`; local `.ts` deleted only after a verified
  `.mkv` (mirrors the existing `compress_file` "delete original only on success").
- **HMAC:** worker signs the notify body (`X-E2P-Signature: sha256=<hmac>`);
  secret shared out-of-band, never written to storage; e2proxy verifies with
  `hmac.compare_digest`.
- **Azure auth on the VM = managed identity** (no keys in `customData`). e2proxy
  on the Pi uses **SAS URLs** (ARM returns them as outputs).

### Job artifacts (under `jobs/<job_id>/`)
`source.ts` (e2proxy) · `job.json` manifest (e2proxy) · `output.mkv` (worker) ·
`done.json` status+sha (worker).

### State machine (e2proxy `/data/external_transcode_state.json`)
`uploading → queued → (transcoding) → downloading → completed | failed | stuck`.

## 5. Key code locations

- `e2proxy.py` → "External Transcoding Module" after `has_compression_backlog()`
  (~line 3277), before `_write_nfo`. Symbols: `EXT_DEFAULTS`,
  `get_external_transcode_config()`, `FilestoreTransport`, `AzureRestTransport`,
  `_ext_transport()`, `_ext_config_ready()`, `external_transcode_submit()`,
  `_ext_handle_completion()`, `_external_transcode_loop()`,
  `start_external_transcode_scheduler()`, `external_transcode_status()`.
- Endpoints: `POST /api/external-transcode/notify` (HMAC) in `do_POST`;
  `GET /api/external-transcode/status` in `_do_GET_inner`. Scheduler wired in
  `run()` near `start_compression_scheduler()`. Gate at start of
  `_compression_scheduler_loop`.
- `workers/external_transcode_worker.py`: `Config` (env vars), `FilestoreTransport`,
  `AzureTransport` (3 auth modes), `transcode()`, `notify()` (HMAC), `process()`
  (claim/heartbeat/transcode/upload/notify/dead-letter), `main()`.
- Config under `external_transcode` in `/data/config.json`; profiles reuse
  `COMPRESSION_PROFILES` (fast/balanced/quality).

## 6. Environment quirks (important)

- **ffmpeg** could not be installed in the dev sandbox (a foreign
  `brew install ffmpeg` held the lock; no docker/node). Tests therefore use a
  **fake ffmpeg** bash script. Real-ffmpeg transcode is a manual smoke test.
- **No `az` CLI / no Azure credentials** in the sandbox → `az deployment group
  validate` and real deploys are **manual** steps (documented in `deploy/README.md`).
- `pip` isn't on PATH; use `python3 -m pip`. Python is 3.9.

## 7. Open items / next steps

1. **Worker script URL branch.** ARM `workerScriptUrl` defaults to the `main`
   branch. While the feature lives on a branch, either override `workerScriptUrl`
   with the branch raw URL or merge the worker to `main` first.
2. **Real-Azure smoke test**: deploy the template, push a job from e2proxy with
   real SAS URLs, confirm a round-trip.
3. **Real-ffmpeg transcode** on a machine that has ffmpeg.
4. Optional: Settings-UI panel (today it's `config.json` + status API);
   Azure-VM auto-start from e2proxy (Azure API) instead of manual start;
   block-upload for sources > 5 GiB.

## 8. Checkpoint / "don't lose the research" best practices

We keep research durable in **three layers**:

1. **Session plan + checkpoints** (assistant session state):
   `~/.copilot/session-state/<id>/plan.md` holds the living plan;
   `checkpoints/` holds point-in-time snapshots with an `index.md`. These survive
   context compaction within the assistant. They are *not* in git, so they're for
   the working session, not cross-device.
2. **This in-repo handoff doc** (`docs/external-transcoding-handoff.md`) +
   feature docs (`README.md`, `workers/README.md`, `deploy/README.md`). This is
   what travels across devices via git. **Update it at each milestone.**
3. **Git history**: small, frequent, well-described commits on the feature
   branch, pushed to GitHub. The branch *is* the durable record.

**Routine when pausing / switching devices:**
1. Make sure tests pass and code compiles.
2. Update this handoff doc's status table + "Open items".
3. Commit with a descriptive message and push the branch.
4. On the new device: `git fetch && git checkout <branch> && git pull`, re-read
   this file, re-run the verification commands in §3.
