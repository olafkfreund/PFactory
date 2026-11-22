---
status: approved
issue: 1108
spec: spec/2026-11-22-1108-pf-spa-history-fallback.md
coders:
  1: codex  # small, security-adjacent logic port; must keep asset/API 404s
  2: codex  # the test for step 1, same context
reviewers:
  - codex   # read-only diff review
  - agy     # independent second read
---

# Plan: serve the SPA shell for client-side routes

Approved decisions: port TFactory's #878 history fallback into PFactory's `SPAStaticFiles`; prefixes `/api`, `/ws`, `/assets`, `/openapi.json`, `/docs`, `/redoc` keep their 404; a path whose last segment has a `.` keeps its 404; otherwise a 404 GET serves `index.html`; existing cache headers unchanged.

## Steps

1. `apps/web-server/server/main.py` (around 583-605): add module-level `_NO_SPA_FALLBACK` and `_wants_spa_shell` (copy TFactory's text and docstring, cite #1108), and make `SPAStaticFiles.get_response` catch `starlette.exceptions.HTTPException` 404 and retry with `"index.html"` when `_wants_spa_shell(scope.get("path") or "")`. If `SPAStaticFiles` is nested inside `create_app`, the helpers may stay module-level. Check the server's router prefixes; if any server-owned prefix outside the list exists (e.g. `/health`, `/metrics` served by routes are fine since routes match first), note it. → verify by step 2's test.
   Traps: routes registered before the mount win, so only paths that fall to the mount are affected; don't change the cache policy.
2. `apps/web-server/tests/test_spa_history_fallback.py` (new): mount `SPAStaticFiles` on a temp dir with `index.html` and `assets/app.js`; assert `/p/demo`, `/settings` → 200 HTML with `no-cache`; `/assets/app.js` → 200 immutable; `/assets/missing.js`, `/api/nope`, `/missing.ico` → 404. If `SPAStaticFiles` is only reachable via `create_app`, move the class to module level (as TF does) so it can be imported. → `apps/backend/.venv/bin/pytest apps/web-server/tests/test_spa_history_fallback.py -q`
   Traps: the ratchet gates committed changes (scripts/ratchet_lint.py).

## Tests

`apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q` green; ratchet passes.

## Rollback

Revert the commit; behaviour returns to JSON 404 on deep links.
