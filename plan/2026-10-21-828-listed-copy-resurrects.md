---
status: approved
issue: 828
spec: spec/2026-10-21-828-listed-copy-resurrects.md
---

# Plan: an edit on one replica must not bring back a session deleted on another

Decisions carried from the approved spec:

- **The fix:** in `apps/backend/plan/service.py`, `_load_from_store()`
  drops the cached copy when the store has no row and the copy's
  `_store_version` is not `0`, then returns `None` as today. `get()` is
  unchanged and raises "unknown session".
- Store errors and version-0 copies (created here, insert not landed) keep
  the cache fallback.
- No `SessionStore` interface change.
- **Rejected:** keeping versions in `_all_sessions()`; a sentinel return;
  raising in `_upsert_session()`.

## Steps

1. `apps/backend/plan/service.py`: in `_load_from_store()`, on `row is None`,
   under `self._store_lock`, pop the cached entry when it exists and its
   `_store_version != 0`. Update the docstring. (coder: agy)
2. `tests/test_plan_stale_write.py`: add
   `test_an_edit_after_a_delete_on_another_replica_does_not_resurrect_it`:
   two services over one `FakeSessionStore`; A ingests, B lists, A discards
   and deletes, then B `get()` raises `PlanServiceError`, B `update_plan()`
   raises `PlanServiceError`, and the id is not in `store.rows`.

## Tests

`ruff check` and `ruff format --check` on the two files;
`pytest tests/test_plan_stale_write.py tests/ -k "session or stale or delete"`
then the full `pytest tests/`. The new test must fail on origin/dev.

## Deviations

- Step 1 was handed to agy, which proposed the same change but could not
  write in its sandbox; it was applied in-session from that proposal.
- Step 2 was written in-session before step 1, to show it fails on
  origin/dev.
- `tests/test_approve_access.py` plants a local-only session in the cache.
  Under the Postgres CI job the service has a store, so that copy (no
  version) was evicted. The test now marks it version 0, "created here",
  which is the contract the fix keeps.

## Rollback

Revert the PR; no data or schema change.
