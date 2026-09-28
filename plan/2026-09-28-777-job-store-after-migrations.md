---
status: draft
issue: 777
spec: spec/2026-09-28-777-job-store-after-migrations.md
---

# Plan: a pod that migrates on boot also gets the durable job-state store without a restart

Self-contained summary of the approved decisions:

- `plan/service.py`: rename `attach_session_store_after_migrations` to
  `attach_stores_after_migrations() -> bool`.
  - Keep `attach_session_store_after_migrations = attach_stores_after_migrations`
    as an alias, with a comment to remove it after 0.6.22.
  - After the session-store block and before the replica guard: if
    `service._job_store is None`, call `_resolve_job_store()`. If that returns
    a store, assign it and log INFO "plan state attached to the durable
    job-state store after boot migrations (#777)".
  - The return value is unchanged (session store attached).
  - The docstring covers both stores.
- `server/main.py`: the lifespan calls `attach_stores_after_migrations` via
  `asyncio.to_thread` right after `init_db()`.
- Nothing is in flight during lifespan startup, so this is a plain
  assignment.
- `guides/shipping.md`: one line on the job store and its log line.
- No new "unavailable" mark for the job store (`_resolve_job_store` already
  re-checks), no tuple return, no separate hook.

All work happens in the worktree `/tmp/.../scratchpad/pf-777` on
`fix/777-job-store-after-migrations`. Symlink the backend venv into it first
(`ln -s /mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv apps/backend/.venv`,
never staged), so the pre-commit ratchet and the P1 suite are real (#786).
Postgres: a dedicated container with a database whose name contains `test`,
stopped afterwards.

## Steps

1. **Tests first,** in `apps/web-server/tests/test_store_after_boot_migrations.py`:
   - (1) attach after migrating sets `_job_store` and logs the #777 line;
   - (2) the app-level boot test also asserts `SERVICE._job_store` is not None
     after startup;
   - (3) the alias identity;
   - (4) a second call keeps the same `_job_store`.

   → verify: on the current code, (1) and (2) fail on the job-store assertion,
   and (3) fails on the missing name. The existing #774 tests keep passing.

2. **`plan/service.py`:** the rename, the job-store block, the alias and the
   docstring.
   → verify: all tests in the file pass.

3. **`server/main.py`:** switch the lifespan import and call to the new name.
   → verify: the app-level test passes; `grep` shows no other caller of the
   old name outside the alias and #774's tests.

4. **Docs:** add the `guides/shipping.md` line.
   → verify by grepping for "(#777)".

5. **Gates:**
   - ci.yml per-process: `pytest tests/ apps/web-server/tests/ -m "not slow"`;
   - store mode: `DATABASE_URL=<pg16 test db> pytest tests/ -m "not slow and not postgres"`
     (#779's conftest migrates and isolates);
   - `TEST_POSTGRES_URL=... pytest tests/postgres -m postgres`, with the P1
     suite NOT skipped;
   - `scripts/ratchet_lint.py --base origin/dev --package apps/backend
     --package apps/web-server --package scripts`;
   - `uvx ruff@0.15.17` format and check on the changed files.

   Commit with the hook, never `--no-verify`.
   → verify all green.

6. **PR** to `dev` with `Fixes #777`, linking the intent, spec and plan. Merge
   when the required checks are green and review threads are addressed. File a
   follow-up issue: "remove the `attach_session_store_after_migrations` alias
   after 0.6.22".
   → verify CI and the issue number.

7. **Release 0.6.22:**
   - a `chore/release-0.6.22` PR into `dev` bumps CHANGELOG, `apps/backend/__init__.py`,
     both `package.json` files and the `package-lock.json` root and workspace
     versions, with `scripts/validate-release.js 0.6.22`;
   - then a `dev -> main` sync PR and `deploy.yml` watched to success.

   → verify in production: the new image is running; the startup log shows
   "PFactory plan state is DURABLE" and "plan sessions are SHARED", with no
   "not ready" warning. (There is no migration in this release, so no #777
   attach line is expected.)

   **The KEDA pin is not changed.**

## Tests

```bash
cd <pf-777 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
pytest apps/web-server/tests/test_store_after_boot_migrations.py -q -o asyncio_mode=auto
pytest tests/ apps/web-server/tests/ -m "not slow" -q
DATABASE_URL=<pg16 test db> pytest tests/ -m "not slow and not postgres" -q
TEST_POSTGRES_URL=<pg16 test db> pytest tests/postgres -m postgres -q
python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

Revert the fix commit on `dev` and cut a patch release. There is no schema or
config change. After a rollback, a boot that migrates `job_states` again needs
a restart to get the durable job store, which was the pre-fix behaviour.
