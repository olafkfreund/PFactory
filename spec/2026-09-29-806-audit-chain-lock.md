---
status: draft
issue: 806
intent: intent/2026-09-29-806-audit-chain-lock.md
---

# Spec: audit hash-chain writes are serialized and unambiguously ordered

Decisions carried from the approved intent:

1. Order the chain by a monotonic sequence column.
2. Use one global chain lock.

## Design

### Facts from the code (`origin/dev`, 2026-09-29)

- `AuditLog` (`apps/web-server/server/database/models.py:534`): `id` is a
  UUID string, and `created_at` is `server_default=func.now()` (the
  transaction start time in Postgres). There is no ordering column.
- Hashing (`services/audit_chain.py`): `_canonical` covers `id`, `action`,
  `user_id`, `org_id`, `resource_type`, `resource_id`, `created_at` and
  `details_json`. Adding a field to it would break older rows, so a new column
  must stay OUT of the hash.
- The chain is read or ordered by `created_at` in four places:
  - `audit_service.py:169` (the head read, with no lock);
  - `audit_export.py:82` and `:107` (JSON and CSV export);
  - `gdpr.py:155` (erasure re-chain).

  `routes/audit.py:115` sorts a display listing by `created_at DESC`; that is
  not chain logic.
- The Alembic head is `d4a7e2b9f1c6` (`20260924_..._plan_session_lease_version.py`),
  in `apps/web-server/server/database/alembic/versions/`.

### The column

- A new `audit_logs.chain_seq BIGINT`, NOT NULL after backfill, with a UNIQUE
  index `ux_audit_logs_chain_seq`.
- It is **assigned by the app, under the lock:** `chain_seq = head.chain_seq
  + 1`, or 1 for the first row. It is exact and gapless, and it is the same
  code on SQLite and Postgres, with no sequence object.
- The UNIQUE index is the backstop: if two writers ever got past the lock
  (for example, a code path that skips it), the second insert fails instead
  of silently forking. That error goes through the existing savepoint and
  warning path.
- It is **not hashed.** `_canonical` is unchanged, so every existing
  `prev_hash` stays valid.

### The migration (`down_revision = "d4a7e2b9f1c6"`)

1. Add `chain_seq` as nullable.
2. Backfill it from `ROW_NUMBER() OVER (ORDER BY created_at, id)`. That is the
   order the existing chain was built and verified in, so the chain still
   verifies (production has 1 row).
3. Make it NOT NULL and create the unique index. SQLite uses
   `batch_alter_table`, following the existing migrations.

Downgrade drops the index and the column.

### The lock (`log_audit_event`, inside the existing savepoint)

- On Postgres, run `SELECT pg_advisory_xact_lock(:key)` before the head read.
  `key` is a fixed bigint constant `AUDIT_CHAIN_LOCK_KEY` in
  `audit_chain.py`.
- A transaction-scoped lock is held until the caller's top-level commit or
  rollback. That covers the window where the row is written but not yet
  visible to others.
- On other dialects, skip it. SQLite allows only one writer at a time.
- Head read: `ORDER BY chain_seq DESC LIMIT 1`. Under READ COMMITTED, this
  statement runs after the lock is acquired, so it sees the previous holder's
  committed row.
- The dialect comes from `db.bind.dialect.name`.
- `log_audit_event_bg` calls `log_audit_event`, so it is covered too.

### Readers

- `audit_export.py`: both queries order by `chain_seq`. The `from`/`to`
  filters on `created_at` are unchanged.
- `serialize_for_export` includes `chain_seq`. For CSV, `CSV_COLUMNS` appends
  `chain_seq` at the end, so existing column positions do not move.
- `gdpr.py`: take the same lock (Postgres) before the re-chain, and walk rows
  by `chain_seq`.
- `verify_chain`: unchanged. Its docstring says rows must be in `chain_seq`
  order.
- The display listing in `routes/audit.py` is left alone (it is not chain
  logic).

### The stale comment

Replace the "v1.0 mitigates via the FastAPI single-replica constraint" comment
with one stating the lock and `chain_seq` contract.

## Alternatives rejected

- **`SELECT ... FOR UPDATE` on the head row** (the original code comment's
  plan): under READ COMMITTED, a waiter re-checks only the locked row. Its
  `ORDER BY ... LIMIT 1` does not pick up the row the other writer inserted,
  so it still links to the old head. It also cannot lock anything on an empty
  table (the genesis race).
- **A Postgres `BIGSERIAL`/identity:** `nextval` is not rolled back and can be
  taken out of commit order unless it is also taken under the lock. It adds
  gaps and a sequence object for no gain over head + 1.
- **`clock_timestamp()` for `created_at`:** rejected in the intent; it still
  depends on clock resolution, and changing `created_at` semantics would touch
  the hash input.
- **SERIALIZABLE isolation for audit writes:** it would mean changing the
  caller's transaction isolation, and retrying the caller's business work.
- **Per-org locks:** rejected in the intent; the chain is global.

## Risks

- **Lock hold time.** The advisory lock is held until the caller's
  transaction ends, so a slow caller transaction delays other audit writers.
  Audit writes are rare (1 production row). Callers commit at the end of the
  request.
- **Deadlock.** The lock is taken mid-transaction, so a transaction that
  already holds row locks could deadlock with another audit writer. Postgres
  detects it and aborts one statement. The savepoint catches it, and the
  audit row is lost with a warning, which is today's failure behaviour. This
  is unlikely at this volume, and the Postgres test does not exercise it.
- **An export consumer that parses CSV by column count.** The new column is
  appended at the end. JSON gains a key.
- **The migration on a large table:** a single `UPDATE` over every row.
  Production has 1 row.

## Verification

- A new `tests/postgres/test_audit_chain_concurrency.py` (`-m postgres`):
  - 8 concurrent writers, each in its own session and transaction. Each
    writes, sleeps briefly before committing (to widen the race window), then
    commits.
  - Then `verify_chain` over the rows in `chain_seq` order passes, and
    `chain_seq` is exactly 1..8.
  - A second case runs the same writers against an empty table (the genesis
    race).
  - Run it first against a variant with the lock disabled, to prove the test
    catches the fork.
- The migration, on Postgres: upgrade a database whose rows have out-of-order
  inserts; the backfill follows `created_at, id`; downgrade works.
- `tests/audit/` (SQLite) stays green, including the export, erasure and
  verify-script tests. The CSV test gains a `chain_seq` check.
- Gates:
  - ci.yml per-process;
  - store mode on Postgres 16;
  - `tests/postgres` with the P1 suite not skipped, against a database named
    `pfactory_test`;
  - the ratchet in CI form;
  - ruff 0.15.17.
- Production after release: `alembic current` shows the new head, the single
  existing row has `chain_seq = 1`, and a JSON export verifies with the CLI.
