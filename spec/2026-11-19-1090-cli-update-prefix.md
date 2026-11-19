---
status: approved
issue: 1090
intent: intent/2026-11-19-1090-cli-update-prefix.md
---

# Spec: user-prefix CLI installs

## Design

- Install npm CLIs with `npm install -g --prefix ~/.local/cli-npm <pkg>`. `~/.local` is a mounted writable volume in the service pod (factory-gitops `home-local`, emptyDir), so a restart falls back to the image pin; the pin stays authoritative.
- Put `~/.local/cli-npm/bin` first on the server's PATH at import (`prefer_user_cli_path`). The gitops pod PATH is an explicit env that lacks it, so doing it in-process needs no gitops change, and spawned agent subprocesses inherit it. Detection (`shutil.which`) then finds the updated CLI before the image copy, which fixes the stale-version display.
- Install, detection and the latest check all use one package per CLI (`@openai/codex`, `@google/gemini-cli`); gemini's `antigravity` alias symlink is created in the same prefix.
- Copilot becomes `kind="npm"` (`@github/copilot`), so Install and the latest check work; node >= 24 in the image satisfies its engine.
- Return the real npm error (tail) in the install response, as `provider_runtime.run_install` already does (Factory#780: authenticated operator route); the UI shows it.
- Bump the Dockerfile pins to codex 0.170.0 and gemini-cli 0.69.0.

## Alternatives rejected

- Gitops-only (writable `.npm-global` volume or PATH env): needs a cross-repo rollout and leaves local/dev runs broken.
- Persisting the prefix on the data PVC: an old user copy would shadow future image bumps.
- Unifying CLI Accounts and Provider Runtimes installers: larger than the bug.

## Risks

- Agent Jobs launched as separate pods do not see the server's prefix; they use the image copy (which is what the pin bump updates).
- Pin bump needs the image canary; versions are the current npm latest.

## Verification

Backend tests that fail without the fix: install argv carries the user prefix, an updated user copy shadows the image copy, the real npm error is returned, copilot is an npm runtime. `tsc --noEmit` for the UI.
