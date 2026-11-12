<!-- pi-ops-template: copilot-instructions v1 · Source: bjoernhoefer/raspberrypi_management templates/app/.github/copilot-instructions.md -->
# Copilot instructions for bjoernhoefer/e2proxy

This repository is deployed to a Raspberry Pi (`wien-streamer`, more hosts via `deploy/targets.json`) through the
central pi-ops platform (`bjoernhoefer/raspberrypi_management`). Binding rules, in this order:

1. **Central standard** – `docs/standards/app-cicd.md` in raspberrypi_management (read via
   `gh api repos/bjoernhoefer/raspberrypi_management/contents/docs/standards/app-cicd.md --jq .content | base64 -d`).
2. **`.specify/memory/constitution.md`** – reliable · slim · fast · durable · modular; reviewer personas in `.github/agents/`.
3. **`AGENTS.md`** in this repo – layout, extension interface, section "Deployment (pi-ops standard)" incl. the
   app-specific quality gates and resource profile. Keep those tables current when behaviour or resource usage changes.

Always:
- Never touch the per-host deploy steps in `.github/workflows/build-deploy.yml`, add self-hosted jobs, SSH into the Pi or
  bypass `pi-deploy`. Guardrail/budget/selftest changes = PR to `inventory/apps.yml` in raspberrypi_management.
- Keep the Pi in mind: arm64 image, all dependencies in the image, RAM budget 512 MB, no heavy work at start.
- Every functional change keeps the selftest (`python3 /app/selftest.py`) and `test.yml` meaningful – extend them with the change.
- Document every change in `README.md` (operation/deployment) and in the changelog on the Help page
  (`e2proxy/webui/page_help.py`: version, ISO 8601 date, what, why).
- Missing information: ask; if not possible, mark as `⚠️ OFFEN: …` and list it in the PR.
- No secrets in code; workflow inputs via `env:`, never `${{ … }}` directly in `run:`.
