---
status: draft
issue: 828
---

# Intent: an edit on one replica must not bring back a session deleted on another

## Problem

With a shared session store, replica B lists sessions (`_all_sessions()`
caches the listed copies with no row version). Replica A then deletes one.
When B edits it, `get()` asks the store, finds no row, and falls back to B's
cached copy, because `_load_from_store()` returns `None` both for a store
error and for a missing row. The write then has no version, so
`_upsert_session()` "learns" it from the store, gets 0 for the missing row,
and inserts it again. The deleted session comes back.

## Outcome

After a delete on any replica, a read or edit of that session on any other
replica answers "unknown session" (404) and writes nothing.

## Affected

`apps/backend/plan/service.py` (`PlanService` read path) and the
multi-replica deployments that set a shared session store.

## Constraints

- No change to the `SessionStore` interface.
- A store error must still fall back to the cached copy (best-effort reads).
- A session this process created whose first insert has not landed
  (version 0) must stay readable from the cache.

## Open questions

None.
