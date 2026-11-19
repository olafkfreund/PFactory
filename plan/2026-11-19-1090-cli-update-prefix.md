---
status: draft
issue: 1090
spec: spec/2026-11-19-1090-cli-update-prefix.md
reviewers:
  - codex   # independent read-only review of the diff
---

# Plan: user-prefix CLI installs

Decisions (from the approved spec): npm CLIs install to `~/.local/cli-npm` via `--prefix`; its `bin` is prepended to the server PATH at import; one package per CLI for install, detection and latest; real npm error returned and shown; Dockerfile pins codex 0.170.0 / gemini-cli 0.69.0; copilot is an npm runtime.

## Steps

1. apps/backend/provider_runtime.py, apps/web-server/server/routes/cli_accounts.py: add `user_cli_prefix()` / `prefer_user_cli_path()`, use `--prefix` in the npm install, symlink `antigravity` in the user prefix, return the npm error tail; copilot kind npm -> verify by the new tests.
   Traps: do not touch models.ts, phase_config.py or project routing.
2. apps/frontend-web CLIAccountCard.tsx, LLMAccountsSettings.tsx, ProviderRuntimesSection.tsx: show the install error -> verify by `npx tsc --noEmit`.
3. Dockerfile: bump codex and gemini-cli pins -> verify by grep.
4. tests: install argv/prefix, shadowing, error surfaced, copilot npm.

## Tests

`pytest` on the touched test files passes; the new tests fail on the base commit.

## Rollback

Revert the commit; the image pin is unchanged in behaviour.
