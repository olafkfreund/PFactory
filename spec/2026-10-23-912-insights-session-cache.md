---
status: approved
issue: 912
intent: intent/2026-10-23-912-insights-session-cache.md
---

# Spec: insights chat keeps every message across pods

## Design

Delete the `_sessions` cache from `InsightsService`:
- the attribute (`:107`);
- its read in `_load_session` (`:158-159`) and its writes (`:154`, `:191`);
- the eviction in delete (`:276-277`).

Every load then reads the file on the shared PVC. Each caller already loads a session, mutates it and saves it, so nothing depends on two loads returning the same object.

## Alternatives rejected

- **Re-read when the file's mtime changes:** more code for no real gain. Sessions are small JSON files, read once per message.
- **Session affinity on the Service:** gitops-only and partial. It breaks on scale-down and failover.

## Risks

- Two pods streaming into the same session at the same moment still give last-writer-wins. That needs one user sending from two tabs that land on different pods. Accepted.
- One extra small file read per call.

## Verification

`tests/test_insights_session_cache.py`: two `InsightsService` instances share one project directory and alternate saving messages. The final file holds all of them. The test fails with the cache in place.
