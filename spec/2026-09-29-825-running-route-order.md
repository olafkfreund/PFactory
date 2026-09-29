---
status: draft
issue: 825
intent: intent/2026-09-29-825-running-route-order.md
---

# Spec: GET /api/tasks/running reaches the running-tasks handler

Decision carried from the approved intent: mount `execution.router` before
`tasks.router`.

## Design

### The change (`apps/web-server/server/main.py:300-302`)

Swap the two `include_router` lines, so `execution.router` is registered first
(prefix `/api/tasks`), then `tasks.router`. The existing comment "Execution
routes also under /api/tasks for frontend compatibility" moves with its line.
A second comment says why the order matters: `tasks.router`'s `GET
/{task_id}` would otherwise catch `/running` (#825).

### Why nothing else moves

`execution.router` declares these routes (`execution.py`):
- `GET /running`
- `GET /{task_id}/status`
- `GET /{task_id}/running`
- `POST /{task_id}/start`, `/stop`, `/recover`
- `POST /create-and-run`

None is a single-segment dynamic path, so none can capture a `tasks.router`
path like `GET/PUT/PATCH/DELETE /{task_id}`. The one shared two-segment path,
`/{task_id}/status`, is `GET` on the execution router and `PATCH` on the
tasks router. Starlette only takes a full (path and method) match, so the
order does not change which handler serves either method.

### The guard (`apps/web-server/tests/test_task_route_order.py`)

The test resolves routes on the real `create_app()` app, without sending
requests (no auth, no database). For a `(method, path)` pair it walks
`app.router.routes` in order and returns the endpoint of the first route
whose `matches(scope)` is `Match.FULL`, exactly as Starlette dispatches. It
asserts:

- `GET /api/tasks/running` → `execution.get_running_tasks` (fails today:
  `tasks.get_task`);
- `GET /api/tasks/p:1` → `tasks.get_task` (the swap does not steal it);
- `GET /api/tasks/p:1/status` → `execution.get_task_status`, and `PATCH
  /api/tasks/p:1/status` → the tasks status updater;
- `POST /api/tasks/create-and-run` → `execution.create_and_run_task`.

## Alternatives rejected

- **Moving `GET /running` into `tasks.router`:** rejected in the intent. It
  splits the execution handlers across two modules.
- **A request-level test through `TestClient`:** it needs auth, settings and
  a database, and it cannot say which handler matched, only what came back.
  The route-resolution test pins the exact bug.

## Risks

- **Another route I missed is shadowed by the swap:** the test covers each
  shared shape, and the full suites run.
- **The route list is private-ish Starlette API:** `app.router.routes` and
  `Route.matches` are stable and public in Starlette. If they change, the
  test fails loudly rather than passing vacuously, because every case expects
  a specific endpoint.

## Verification

- The new test fails on the current code on the `/running` case only, and
  passes after the swap.
- ci.yml per-process, and store mode (no DB change, so the Postgres suite
  runs once).
- The ratchet in CI form, and ruff 0.15.17.
- Production after release: `GET /api/tasks/running` with the API token
  returns 200 and a list.
