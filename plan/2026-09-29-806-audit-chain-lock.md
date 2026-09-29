---
status: draft
issue: 806
spec: spec/2026-09-29-806-audit-chain-lock.md
---

# Plan: audit hash-chain writes are serialized and unambiguously ordered

Self-contained summary of the approved decisions:

- **Column:** `audit_logs.chain_seq BIGINT NOT NULL`, with the unique index
  `ux_audit_logs_chain_seq`.
  - Set by the app under the lock: head + 1, or 1 for the first row.
  - Not part of the hash: `_canonical` is unchanged.
- **Migration** (`down_revision = "d4a7e2b9f1c6"`):
  - add the column as nullable;
  - backfill it with `ROW_NUMBER() OVER (ORDER BY created_at, id)`;
  - make it NOT NULL and add the unique index (`batch_alter_table` for
    SQLite);
  - downgrade drops both.
- **Lock:** on Postgres only, `SELECT pg_advisory_xact_lock(AUDIT_CHAIN_LOCK_KEY)`
  before the head read, inside the existing savepoint.
  - The dialect comes from `db.bind.dialect.name`.
  - The head read is `ORDER BY chain_seq DESC LIMIT 1`.
  - `AUDIT_CHAIN_LOCK_KEY` is a fixed bigint constant in `audit_chain.py`.
- **Readers:**
  - both `audit_export.py` queries order by `chain_seq`;
  - `serialize_for_export` includes `chain_seq`, and `CSV_COLUMNS` appends
    it at the end;
  - `gdpr.py` takes the same lock and re-chains by `chain_seq`;
  - the `verify_chain` docstring says rows must be in `chain_seq` order;
  - the display listing in `routes/audit.py` is untouched.
- **Comment:** replace the stale "single-replica" comment with the lock and
  `chain_seq` contract.
- **Rejected:** `FOR UPDATE` on the head, a BIGSERIAL, `clock_timestamp()`,
  SERIALIZABLE isolation, per-org locks.

### Correction to the spec (found while planning)

The spec says `log_audit_event_bg` "calls `log_audit_event`, so it is covered
too". **It does not.** `audit_service.py:232-244` builds its own `AuditLog`
with no `prev_hash`, no `retention_until` and (after this change) no
`chain_seq`. It is the path the MCP write routes use
(`mcp_stdio/router.py:111`). The only production row
(`mcp.task.create_and_run`, 2026-08-19) came from it with `prev_hash` NULL.

Today every background row breaks the chain. After the migration, every one
would fail the NOT NULL constraint and be dropped with a warning.

**Fix, within the spec's intent:** `log_audit_event_bg` opens its session,
calls `log_audit_event(session, ...)` and commits. It then gets the lock,
`prev_hash`, `chain_seq` and retention from the one write path. Its signature
and its warning-on-failure behaviour are unchanged.

The existing production row keeps `prev_hash` NULL. As the first row,
`verify_chain` reads NULL as GENESIS, so it still verifies, and the backfill
gives it `chain_seq = 1`.

All work happens in the worktree `/tmp/.../scratchpad/pf-806` on
`fix/806-audit-chain-lock`, with the backend `.venv` symlinked in and never
staged. Postgres runs use a dedicated pg16 container with a database named
`pfactory_test`, stopped afterwards.

## Steps

1. **Tests first.**
   - New `tests/postgres/test_audit_chain_concurrency.py`, marked postgres and
     slow, using the `test_postgres_url` fixture, against a migrated
     database. Two tests:
     - (i) with 8 concurrent writers against an empty table, each in its own
       session: `log_audit_event`, sleep 50 ms, commit. Expect exactly 8 rows,
       `chain_seq` 1..8, and `verify_chain` passing in `chain_seq` order;
     - (ii) the same with 8 more writers on top of an existing chain.
   - A SQLite test in `tests/audit/` that `log_audit_event_bg` writes a row
     with a non-null `prev_hash` that chains, and `chain_seq` set.
   - The CSV export test asserts that `chain_seq` is the last column.

   → verify: on the current code, (i) and (ii) fail (there is no `chain_seq`
   column, and the chain forks), and the background-path test fails.

2. **Model and migration:** add `chain_seq` to `AuditLog` and a new file in
   `apps/web-server/server/database/alembic/versions/`.
   → verify: on Postgres, `alembic upgrade head`, then downgrade by one, then
   upgrade again. A migration test inserts 3 rows with out-of-order
   `created_at` before the upgrade and asserts that `chain_seq` follows
   `created_at, id`.

3. **Write path** (`audit_service.py`, `audit_chain.py`):
   - the lock, the head read by `chain_seq`, and `chain_seq = head + 1`;
   - `log_audit_event_bg` routes through `log_audit_event`;
   - the comment replaced.

   → verify: the step 1 tests pass. Then, with the lock call patched out, (i)
   fails (fewer than 8 rows or a broken chain), which proves the test catches
   the race. Restore the lock.

4. **Readers:** `audit_export.py` (both queries, `serialize_for_export`,
   `CSV_COLUMNS`), `gdpr.py` (the lock and the order), and the `verify_chain`
   docstring.
   → verify: `tests/audit/` passes on SQLite (export, erasure,
   verify-script round trip).

5. **Gates:**
   - ci.yml per-process: `pytest tests/ apps/web-server/tests/ -m "not slow"`;
   - store mode: `DATABASE_URL=postgresql+asyncpg://.../pfactory_test pytest tests/ -m "not slow and not postgres"`;
   - `TEST_POSTGRES_URL=... pytest tests/postgres -m postgres`, with the P1
     suite not skipped;
   - `scripts/ratchet_lint.py --base origin/dev --package apps/backend
     --package apps/web-server --package scripts`, after the commit;
   - `uvx ruff@0.15.17 format --check` on the changed files.

   Commit with the hook, staging named files only.
   → verify all green.

6. **Docs:** in `guides/` (the audit-trail operations guide, if present, else
   `guides/shipping.md`), document:
   - the chain order is `chain_seq`;
   - exports carry `chain_seq` (CSV: last column);
   - writes are serialized by the advisory lock.

   → verify by grepping for "chain_seq".

7. **PR** to `dev` with `Fixes #806`, linking the intent, spec and plan, and
   stating the spec correction above. Merge (merge commit) when the checks are
   green and the threads are addressed.

8. **Release and verify.** Cut a patch release (0.6.23) via a
   `chore/release-0.6.23` PR:
   - CHANGELOG, versions, and the `package-lock.json` root and workspace
     entries;
   - `validate-release.js 0.6.23`;
   - then a `dev -> main` sync and the deploy.

   → verify in production:
   - the migration ran (`alembic_version` is the new head);
   - `SELECT chain_seq, prev_hash IS NULL FROM audit_logs` gives `1|t`;
   - after the next MCP write, a second row has `chain_seq = 2` and a
     non-null `prev_hash`, and a JSON export passes the verify CLI.

## Tests

```bash
cd <pf-806 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
U=postgresql+asyncpg://postgres:pw@localhost:<port>/pfactory_test
TEST_POSTGRES_URL=$U pytest tests/postgres/test_audit_chain_concurrency.py -m postgres -q
pytest tests/audit -q
pytest tests/ apps/web-server/tests/ -m "not slow" -q
DATABASE_URL=$U pytest tests/ -m "not slow and not postgres" -q
TEST_POSTGRES_URL=$U pytest tests/postgres -m postgres -q
python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

- Revert the fix commit and release a patch.
- To drop the column, run `alembic downgrade -1` before deploying the revert.
  Leaving the column in place is harmless to the old code, which ignores it.
  But the NOT NULL constraint would then reject the old code's inserts, so
  either downgrade first or make the column nullable.
- The rows written in between keep valid `prev_hash` links in `chain_seq`
  order.
