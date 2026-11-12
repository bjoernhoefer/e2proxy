# Plan 001

## Constitution Check
| Principle | How this change complies |
|-----------|--------------------------|
| I Reliable | Mechanical, AST-based split (no logic edits); page/API parity verified; recorder isolated, never raises into request threads |
| II Slim | stdlib only; recorder port ~1.2 k lines vs. 4.3 k in the standalone service; no new framework |
| III Fast/Pi | no new work on request paths; recorder loops sleep and check `enabled` per tick; ffmpeg baked into image |
| IV Modular | `e2proxy/` package, `extensions` registry, recorder as first extension |
| V Tests | pytest suite incl. changelog regressions, node JS parse, container selftest |
| VI Durable | GHCR image, pi-deploy guardrails/rollback, build id = git sha |

## Design
1. **Split** – AST splitter assigns each top-level symbol of the monolith to a module, generates
   imports; reassigned globals accessed module-qualified; cycles resolved by function-local imports.
2. **Extensions** – `ProxyHandler.do_GET/POST/PUT/DELETE` call `extensions.dispatch` first; legacy
   routes are the fallback.
3. **Recorder** – `engine.Recorder` with injected `api`/`clock`/`sleep`; talks to e2proxy's own
   `/api/record/*`, `/api/health`, `/api/epg/data` over HTTP (same contract as the remote proxies)
   so local and remote backends are treated identically. State: `DATA_DIR/recorder/{series,recordings}.json`.
4. **Deploy** – Dockerfile + `selftest.py`; template workflows; `targets` job → matrix.

## Complexity Tracking
| Deviation | Why |
|-----------|-----|
| Deploy job is a matrix (template has a single host) | user requirement: several Pis; per-host steps unchanged |
| Changelog stays on the Help page instead of `CHANGELOG.md` | it is the project's existing, user-visible changelog |
