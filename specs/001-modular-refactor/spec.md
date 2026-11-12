# Spec 001 – Modular refactor, integrated recorder, automated Pi rollout

**Status**: implemented (v5.0) · **Branch**: `feature/refactor`

## Problem
`e2proxy.py` had grown to a single 14 k-line file: hard to review, impossible to unit-test,
every feature edited the same handler. The recorder lived in a separate service
(e2recorder) that duplicated EPG/tuner knowledge. Rollouts were manual `scp` + restart, on one Pi.

## User stories
1. *As maintainer* I can find, test and change one concern without touching unrelated code. (P1)
2. *As user* I can enable the recorder in Settings, see planned/recorded broadcasts in the EPG and
   manage series rules – without running a second container. (P1)
3. *As operator* a merge to `main` ships a tested arm64 image to every configured Pi, with
   guardrails and rollback, and I can add a second Pi by adding one JSON entry. (P1)
4. *As contributor* every documented bug has a test, and reviewers with clear focus areas (personas). (P2)

## Requirements
- FR-1 Behaviour of all existing pages and APIs unchanged (verified page/API parity against the monolith).
- FR-2 `python -m e2proxy` and `--selfcheck` replace `python3 e2proxy.py`.
- FR-3 Extension interface: route registry + lifecycle; disabled extension ⇒ routes 404, no threads.
- FR-4 Recorder: config key `recorder.enabled` (default off); series rules (name/regex, channel,
  keep-last, once), EPG scan, dedupe incl. shifted starts, skip/unskip, pre/post buffer, late-start
  tolerance, tuner check, remote proxies, restart recovery, keep-last cleanup, TMDB lookup.
- FR-5 EPG overview marks scheduled/recording/done/skipped/matching broadcasts and offers
  record once / record series / skip in the detail popup (only when enabled).
- FR-6 Settings tab "Recorder" for options, series and recordings.
- FR-7 CI per pi-ops standard (`test.yml`, `build-deploy.yml`), multi-host deploy matrix from `deploy/targets.json`.
- NFR: stdlib only; idle RSS unchanged; image contains ffmpeg (no apt at start-up).

## Out of scope / follow-ups
- Moving the remaining legacy routes of `ProxyHandler` into `extensions` routes, module by module.
- Removing the dead `e2probe is None` branches (probe is now always present).
- Slimmer image (ffmpeg from Debian pulls ~600 MB of libs).
- Upstreaming the multi-host deploy matrix into the pi-ops `build-deploy` template (⚠️ OFFEN).
