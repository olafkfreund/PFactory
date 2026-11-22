---
status: approved
issue: 1108
intent: intent/2026-11-22-1108-pf-spa-history-fallback.md
---

# Spec: serve the SPA shell for client-side routes

## Design

Port TFactory's history fallback (TFactory `apps/web-server/server/main.py`, `_NO_SPA_FALLBACK`, `_wants_spa_shell`, `SPAStaticFiles.get_response`, PR #1000) into PFactory's `SPAStaticFiles` (`apps/web-server/server/main.py:592`):

- `_NO_SPA_FALLBACK = ("/api", "/ws", "/assets", "/openapi.json", "/docs", "/redoc")`.
- `_wants_spa_shell(path)`: false under those prefixes; otherwise true when the last segment has no `.`.
- `get_response`: on a Starlette 404 where `_wants_spa_shell(scope["path"])`, serve `index.html`; then apply the existing cache headers.
- Also take TF's non-HTTP scope guard (`__call__`: close stray websockets instead of asserting) only if PFactory lacks it; otherwise leave `__call__` alone.

## Alternatives rejected

- A FastAPI catch-all route returning index.html: it would shadow API 404s and run before mounts; the mount-level fallback is what TF and AIF use.
- Hash router in the frontend: changes every URL and breaks the phase 4 contract.

## Risks

An API route registered after the mount, or a path outside `/api`, would get HTML instead of JSON 404; the prefix list covers PFactory's server-owned paths (check `app.include_router` prefixes).

## Verification

Unit test with a temp static dir: `/p/x` and `/settings` → 200 index.html; `/assets/missing.js`, `/api/nope`, `/favicon-missing.ico` → 404; index.html carries `no-cache`. Live: after release, `/p/olafkfreund-aifactory-demo` returns 200 from the pod.
