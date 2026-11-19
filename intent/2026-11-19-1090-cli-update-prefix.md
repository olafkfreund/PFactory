---
status: approved
issue: 1090
author: olafkfreund
---

# Intent: CLI Update/Install must work on the read-only image and show real errors

## Problem

Owner report 2026-11-19: in Settings, Update on the Codex and Antigravity CLIs fails and both keep showing "Update available"; Provider Runtimes shows Copilot not installed and Install fails every time.

- `npm install -g` targets `/home/nonroot/.npm-global`, in the read-only image layer (`readOnlyRootFilesystem: true`): EROFS.
- Antigravity is `@google/gemini-cli` under an alias. Its install goes to the writable `~/.gemini/antigravity-cli` and succeeds, but detection resolves `antigravity` via PATH first and finds the image copy, so the UI keeps the old version.
- Copilot is registered as kind `gh`, so Install runs `copilot upgrade`, which cannot run when copilot is absent.
- The server hides the npm error behind an opaque reference id, and the UI ignores failed installs.

## Proposed outcome

Update/Install succeeds into a writable prefix, the new version is what the UI then reports, a failure shows the real npm error, and the image pins are current.

## Affected users and systems

Operators using Settings > Other CLI Accounts and Provider Runtimes; the server pod and the agent subprocesses it spawns.

## Constraints

No kubectl actions. No change to model catalogs or project routing files. The image pin stays the durable source (the writable prefix is ephemeral).

## Open questions

None.
