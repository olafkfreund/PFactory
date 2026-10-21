---
status: draft
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

## Rollback

Revert the PR; no data or schema change.
