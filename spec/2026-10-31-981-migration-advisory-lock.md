---
status: approved
issue: 981
intent: intent/2026-10-31-981-migration-advisory-lock.md
---

# Spec: Postgres advisory lock around boot migrations

## Design

In `init_db()` (`apps/web-server/server/database/engine.py`), the
`MIGRATIONS_AUTO_APPLY=true` branch becomes:

- **Postgres** (`engine.dialect.name == "postgresql"`): open one connection
  from the app `engine` with `isolation_level="AUTOCOMMIT"` (so it is never
  "idle in transaction" while waiting), run
  `SELECT pg_advisory_lock(<fixed bigint key>)`, then run the existing
  `_alembic_upgrade_head_sync` via `asyncio.to_thread`, and in `finally` run
  `SELECT pg_advisory_unlock(<key>)` before the connection closes. The lock is
  session-level, so it is also released if the pod dies mid-upgrade (the
  session ends).
- A second pod blocks on `pg_advisory_lock` until the first finishes. Alembic
  then reads the updated `alembic_version` and finds nothing to do, so each
  revision runs once.
- **SQLite and any other dialect**: no lock, today's behaviour.
- The key is a module constant `_MIGRATION_LOCK_KEY` with a one-line comment.
  It is only shared by processes using the same database, so it cannot clash
  with AIFactory/TFactory unless they share the database and the key.

The `MIGRATIONS_AUTO_APPLY=false` path is unchanged.

## Alternatives rejected

- **Pre-sync Argo hook Job** running migrations once per sync (the issue's
  alternative): needs a gitops change, DDL rights in the Job, and auto-apply turned off in
  the pods. The user chose the lock.
- **Transaction-level lock (`pg_advisory_xact_lock`) inside `env.py`**: Alembic
  opens its own connection and transaction; tying the lock to it touches
  `env.py` for every CLI run too. The session lock in `init_db()` is smaller.
- **`pg_try_advisory_lock` and skip**: a pod that skips could start serving
  before the schema is at head.

## Risks

- A hung migration blocks other booting pods on the lock; they wait instead
  of racing. Kubernetes probes surface it, as they would for a hung upgrade
  today.
- Holds one extra pool connection for the duration of the upgrade at boot only.

## Verification

- New Postgres test in `tests/postgres/test_p1_alembic.py` (markers
  `postgres`, `slow`; runs in the CI `postgres-acceptance` job): drop the
  schema, start two `init_db()` subprocesses at once, assert both exit 0 and
  `alembic_version` has exactly one row at head.
- New unit test (no DB server): with a SQLite URL, `init_db()` does not issue
  `pg_advisory_lock`.
- ruff on touched files; existing `tests/postgres` and SQLite migration tests
  stay green.
