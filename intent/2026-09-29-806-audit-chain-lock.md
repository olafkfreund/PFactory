---
status: approved
issue: 806
author: Olaf Krasicki-Freund
---

# Intent: audit hash-chain writes are serialized and unambiguously ordered

Blocks factory-gitops#273 (lifting the PFactory KEDA one-replica pin).

## Problem

The audit log is a hash chain (Epic #26 P5.2): each row's `prev_hash` is
`compute_hash(previous row's prev_hash, previous row's content)`. `verify_chain`
walks rows in `created_at` order and fails at the first mismatch.

`log_audit_event` (`apps/web-server/server/services/audit_service.py`,
around lines 155-185) reads the chain head with
`SELECT ... ORDER BY created_at DESC LIMIT 1`, with no lock, then inserts a row
linked to it. Two things are wrong:

1. **No serialization.** Two concurrent writers can read the same head, and
   both link to it: the chain forks. The code comment assumes "the FastAPI
   single-replica constraint" protects it. It does not: concurrent requests in
   one process use separate sessions and race the same way. A second replica
   only makes it likelier.
2. **Ambiguous order.** `created_at` is `server_default=func.now()`, which in
   Postgres is the transaction's start time, and `id` is a random UUID. A
   writer whose transaction started earlier, but which wrote its row later,
   gets the earlier `created_at` while linking to the later row. So even
   perfectly serialized writes can verify out of order. Equal timestamps have
   no tie-break at all.

`gdpr.py` (erasure, step 4) re-chains the whole table in `created_at` order.
It has the same ordering ambiguity and can race a concurrent writer.

Production impact today is latent: `audit_logs` has 1 row (2026-08-19). But
the feature exists to be tamper-evident, and a chain that breaks under normal
concurrency cannot tell tampering from a race.

## Proposed outcome

- Concurrent audit writes, from one process or several pods, always produce a
  single linear chain that `verify_chain` accepts.
- The chain order is unambiguous: the row a writer links to always sorts
  immediately before its own row.
- GDPR re-chaining cannot interleave with a concurrent write.
- Proven by a Postgres test with concurrent writers, which fails today.

## Affected users and systems

- `apps/web-server/server/services/audit_service.py` (`log_audit_event`, and
  through it `log_audit_event_bg`).
- `apps/web-server/server/services/gdpr.py` (re-chain).
- `apps/web-server/server/services/audit_chain.py` and the audit export and
  verify CLI, if the sort key changes.
- Possibly an Alembic migration (a new ordering column).
- Operators verifying exported audit packs.

## Constraints

- Audit writes stay in the caller's transaction (the savepoint design) and
  never raise into the caller.
- SQLite (dev and tests without `DATABASE_URL`) keeps working. It has no
  advisory locks, but it also serializes writers.
- Existing chains still verify. Today's single production row, and any
  export already taken, must not become invalid.
- No measurable latency for normal request volumes; audit writes are rare.

## Open questions

Resolved 2026-09-29 (approved): 1 = (a) a monotonic sequence column; 2 = one global chain lock.

1. **Ordering fix:**
   - (a) add a monotonic sequence column (`BIGSERIAL`/identity) assigned
     under the lock, and order the chain by it;
   - (b) keep `created_at` but set it to `clock_timestamp()` inside the lock,
     with `id` as a tie-break.

   **Recommended: (a).** A sequence is exact and cannot tie. It needs a
   migration, and the export format gains the column. (b) avoids the
   migration but still leans on clock resolution.
2. **Lock scope:** one global chain lock (the chain is global today), or per
   org? **Recommended: global,** matching the chain. Per-org chains would be a
   format change.
