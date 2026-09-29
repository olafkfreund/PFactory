---
status: draft
issue: 825
spec: spec/2026-09-29-825-running-route-order.md
---

# Plan: GET /api/tasks/running reaches the running-tasks handler

Self-contained summary of the approved decisions:

- **The fix:** in `apps/web-server/server/main.py`, register `execution.router`
  (prefix `/api/tasks`) **before** `tasks.router` (same prefix), so
  `execution.py`'s `GET /running` is tried before `tasks.py`'s `GET
  /{task_id}`.
  - The existing comment "Execution routes also under /api/tasks for frontend
    compatibility" moves with its line.
  - A new comment says the order is load-bearing (#825).
- **Why it is safe:** `execution.router` has no single-segment dynamic route.
  The only shared path, `/{task_id}/status`, is `GET` on the execution router
  and `PATCH` on the tasks router, and Starlette dispatches only on a full
  path-and-method match.
- **The guard:** a route-resolution test on the real `create_app()` app. It
  sends no requests, so it needs no auth or database.
- **Rejected:** moving `/running` into `tasks.router`; a request-level
  `TestClient` test.

The model split does not apply (2 files, 2 editing steps), so this is
implemented in-session. Work happens in the worktree
`/tmp/.../scratchpad/pf-825` on `fix/825-running-route-order`, with the
backend `.venv` symlinked in and never staged.

## Steps

1. **Test first:** a new `apps/web-server/tests/test_task_route_order.py`.
   - `_endpoint(app, method, path)` walks `app.router.routes` in order and
     returns `route.endpoint` for the first route whose `route.matches(scope)`
     returns `Match.FULL`. The scope is `{"type": "http", "method": method,
     "path": path, "path_params": {}, "root_path": "", "query_string": b"",
     "headers": []}`.
   - One module-scoped `create_app()` is shared by the cases. It follows
     `test_store_after_boot_migrations.py`: settings `DISABLE_AUTH` false,
     and `BACKEND_PATH` pointed at a tmp dir. Only the settings that
     `create_app()` needs to construct; it does not start the lifespan.
   - The cases:

     | Request | Expected endpoint |
     |---|---|
     | `GET /api/tasks/running` | `execution.get_running_tasks` (`execution.py:77`) |
     | `GET /api/tasks/p:1` | `tasks.get_task` (`tasks.py:1075`) |
     | `GET /api/tasks/p:1/status` | `execution.get_task_status` (`execution.py:85`) |
     | `PATCH /api/tasks/p:1/status` | `tasks.update_task_status` (`tasks.py:1318`) |
     | `POST /api/tasks/create-and-run` | `execution.create_and_run_task` (`execution.py:698`) |

   → verify: on the current code, only the `GET /api/tasks/running` case
   fails (it resolves to `tasks.get_task`).

   Traps:
   - `create_app()` reconfigures logging (see
     `test_store_after_boot_migrations.py:58`). Do not use caplog here.
   - New test files must pass mypy `--strict`, and the CI ratchet counts
     every error in a new file.

2. **`main.py:300-302`:** swap the two `include_router` lines, and add the
   #825 comment.
   → verify: all 5 cases pass. `grep -n 'include_router(execution.router'`
   shows it above `tasks.router`.

   Traps: none (no import changes).

3. **Gates:**
   - ci.yml per-process: `pytest tests/ apps/web-server/tests/ -m "not slow"`;
   - store mode on Postgres 16 (a dedicated container with a
     `pfactory_test` database, stopped afterwards);
   - `scripts/ratchet_lint.py --base origin/dev --package apps/backend
     --package apps/web-server --package scripts` (ruff + mypy), after the
     commit;
   - `uvx ruff@0.15.17 format --check` on the two files.

   Commit with the hook, staging named files only.
   → verify all green.

4. **PR** to `dev` with `Fixes #825`, linking the intent, spec and plan.
   Merge (merge commit) when green.

5. **Release** with the next patch (0.6.26, or folded into a pending release
   if one is being cut): the CHANGELOG, versions, `package-lock.json`,
   `validate-release.js`, the `dev -> main` sync and the deploy.
   → verify in production: a read-only `GET /api/tasks/running` with the API
   token, via a `svc/pfactory` port-forward, returns 200 with `{"tasks":
   [...], "count": n}`.

## Tests

```bash
cd <pf-825 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
pytest apps/web-server/tests/test_task_route_order.py -q      # 5 passed after step 2
pytest tests/ apps/web-server/tests/ -m "not slow" -q
python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

Revert the commit. That restores the old order, so `/api/tasks/running` is
unreachable again and nothing else changes.
