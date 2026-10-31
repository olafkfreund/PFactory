---
status: approved
issue: 981
spec: spec/2026-10-31-981-migration-advisory-lock.md
coders:
  1: codex   # lead assigned Codex to this lane
  2: codex
---

# Plan: Postgres advisory lock around boot migrations

Approved decisions (from the spec):
- In `init_db()`, when `MIGRATIONS_AUTO_APPLY` is true and
  `engine.dialect.name == "postgresql"`, hold a session advisory lock on a fixed
  bigint key (`_MIGRATION_LOCK_KEY`) for the whole `alembic upgrade head`, taken
  on one AUTOCOMMIT connection from the app `engine`, released in `finally`.
- SQLite and other dialects: no lock. `MIGRATIONS_AUTO_APPLY=false`: unchanged.
- No Argo hook Job, no `env.py` change, no new dependency.

## Steps

1. `apps/web-server/server/database/engine.py`: add module constant
   `_MIGRATION_LOCK_KEY` (fixed bigint, one-line why comment) near
   `_ALEMBIC_INI_PATH` (~line 103). In `init_db()` (~lines 187-193) replace the
   bare `await asyncio.to_thread(_alembic_upgrade_head_sync)` with: if
   `engine.dialect.name == "postgresql"`, `async with engine.connect() as conn`,
   `conn = await conn.execution_options(isolation_level="AUTOCOMMIT")`,
   `SELECT pg_advisory_lock(:k)`, then `try: await asyncio.to_thread(...)`
   `finally: SELECT pg_advisory_unlock(:k)`; else the bare call as today.
   → verify by `apps/backend/.venv/bin/ruff check apps/web-server/server/database/engine.py`
   Traps: `engine` is created at import from `DATABASE_URL`; tests that need a
   different URL run `init_db()` in a subprocess. Keep `text()` (already imported).
2. Tests:
   a. `tests/postgres/test_p1_alembic.py`: add
      `test_concurrent_init_db_applies_migrations_once` (`@pytest.mark.postgres`,
      `@pytest.mark.slow`): `_drop_schema`, start two `init_db()` subprocesses
      at once (reuse the `_init_db_subprocess` env/code via `subprocess.Popen`
      or a thread pool), assert both exit 0, then query `alembic_version` and
      assert exactly one row equal to the script head.
   b. A unit test, no DB server (`tests/test_migration_lock.py`): run
      `init_db()` in a subprocess against a temp SQLite URL with an
      `after_cursor_execute` listener (or by asserting via a patched
      `_alembic_upgrade_head_sync`) and assert no `pg_advisory` statement is issued.
   → verify by `apps/backend/.venv/bin/pytest tests/test_migration_lock.py -q`
   and, with `TEST_POSTGRES_URL` set to a local Postgres,
   `apps/backend/.venv/bin/pytest tests/postgres/test_p1_alembic.py -m postgres -q`.
   Traps: Postgres tests skip without `TEST_POSTGRES_URL`; CI runs them in the
   `postgres-acceptance` job. Never `git add -A` (venv symlink in worktree).

## Tests

- `ruff check` + `ruff format --check` on touched files: clean.
- `pytest tests/test_migration_lock.py tests/secrets/test_p2_sqlite_migration.py -q`: pass.
- `pytest tests/postgres/test_p1_alembic.py -m postgres` against Postgres 16: pass;
  the new concurrency test fails on `origin/dev` (second process hits duplicate DDL).

## Rollback

Revert the PR; boot migrations go back to unlocked. No schema or data change.
