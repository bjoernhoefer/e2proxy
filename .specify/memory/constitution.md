<!--
Sync Impact Report
- Version: template → 1.0.0 (initial ratification)
- Added: Principles I–VI, Runtime Constraints, Review Personas & Workflow, Governance
- Templates: plan-template "Constitution Check" gates map to principles I–VI (no edits needed)
- Deferred: none
-->
# e2proxy Constitution

## Core Principles

### I. Reliable first
e2proxy is household infrastructure: a stream that breaks while someone watches TV is the worst
outcome. Every change MUST keep the existing behaviour working (live streams, recordings, Plex DVR,
OpenWebif apps, EPG). Failures of one receiver, one extension or one external API (TMDB/TVDB/Plex)
MUST be contained – log, degrade, continue; never crash the process or block a request thread
indefinitely. All network calls carry a timeout. State on disk is written atomically (tmp + replace)
and a corrupt file is quarantined, not fatal.

### II. Slim, not overblown
Python standard library only at runtime (ffmpeg is the one external binary). No framework, no ORM,
no build step for the UI. New code solves the concrete problem at hand – no speculative
abstractions, no config switches nobody asked for. Prefer deleting code over adding it. A feature
that can be an optional extension MUST be one (see IV).

### III. Fast & light on the Pi
Target hardware is a Raspberry Pi (arm64, 512 MB RSS budget incl. ffmpeg children, SD/NVMe storage).
Request paths MUST NOT do blocking receiver probes when a cached answer exists (`/api/health` never
touches a receiver). No heavy work at import or start-up; background loops sleep, they never spin.
Avoid write amplification (logs rotate, caches are batched). Transcoding is the expensive path –
passthrough is the default wherever a client can play the raw TS.

### IV. Modular with open interfaces
The code is a package (`e2proxy/`) with one concern per module. Extensions plug in through
`e2proxy.extensions` (`@route`, `Extension(name, start, enabled)`) instead of growing the HTTP
handler; their routes vanish when disabled. Modules talk to each other through documented
functions, never by reaching into another module's private globals. Optional features
(recorder, probes, diagnostics) MUST be switchable in configuration and cost nothing when off.

### V. Every bug becomes a test (NON-NEGOTIABLE)
Each documented problem (changelog, issue, PR comment) gets a regression test that fails without
the fix. `pytest`, `ruff`, the WebUI self-check (`python -m e2proxy --selfcheck`) and the container
selftest (`selftest.py`) are the merge gate; CI runs all of them. Embedded JavaScript MUST parse
on every page (node `--check` in the test-suite) – a single stray escape once killed a whole UI.

### VI. Durable operations
Releases are reproducible images (`ghcr.io/bjoernhoefer/e2proxy`, arm64) built by CI and rolled
out only through the pi-ops standard (`pi-deploy`: guardrails, selftest, automatic rollback).
Nobody copies files onto a Pi by hand except via the documented emergency path. Every running build
is identifiable (`/api/version`: `VERSION+branch.sha`).

## Runtime Constraints
- Python ≥ 3.11, stdlib only; ffmpeg/ffprobe from Debian in the image.
- Host networking (SSDP multicast, OpenWebif ports 80/8001/81).
- Persistent state lives under `E2PROXY_DATA_DIR` (`/data`); recordings under the configured path.
- UI is server-rendered HTML + vanilla JS, bilingual (EN/DE via `I18N`), dark/light theme.

## Review Personas & Workflow
Every non-trivial change is reviewed from the viewpoint of the personas in `.github/agents/`:
**DVB & Streaming specialist**, **Raspberry Pi enthusiast**, **Python developer**,
**UI developer** (visual changes need a screenshot/rendering confirmed by the user before a major UI
change is merged) and **Documentation & QA** (turns documented problems into tests, decides
minor vs. major together with the others and the user, bumps `VERSION` in `e2proxy/version.py` and
adds the changelog entry on the Help page).

Spec-driven flow (SpecKit): `/speckit.specify` → `/speckit.plan` (Constitution Check against
I–VI) → `/speckit.tasks` → `/speckit.implement`. Specs live in `specs/NNN-name/`.

## Governance
This constitution supersedes other conventions in the repo. Amendments are made by PR, update the
version below (semver: principle removed/redefined = major, added = minor, wording = patch) and
the Sync Impact Report. Complexity that violates II or III must be justified in the plan's
"Complexity Tracking" table.

**Version**: 1.0.0 | **Ratified**: 2026-11-12 | **Last Amended**: 2026-11-12
