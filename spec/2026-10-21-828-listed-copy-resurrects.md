---
status: draft
issue: 828
intent: intent/2026-10-21-828-listed-copy-resurrects.md
---

# Spec: an edit on one replica must not bring back a session deleted on another

## Design

In `PlanService._load_from_store()`, when the store answers with no row (not
an error), drop this process's cached copy of that session unless the copy's
`_store_version` is `0`. Return `None` as today. `get()` then finds no cached
copy and raises `PlanServiceError("unknown session ...")`, which the routes
already map to 404.

- `_store_version` other than 0 means the copy came from the store (a read
  gave a version, a list gave `None`), so a missing row now means another
  replica deleted it.
- `0` means this process created the session and its insert has not landed
  yet; that copy keeps the existing fallback.
- A store error still returns `None` without touching the cache, so the
  best-effort fallback is unchanged.

This also closes the get-after-delete case: `get()` on B after the delete on
A now raises instead of returning the stale cached copy.

## Alternatives rejected

- Keep the cached `_store_version` in `_all_sessions()`: a listed copy that was
  never cached before still has no version, so the resurrection stays.
- A sentinel return from `_load_from_store()`: same behaviour, more code at
  both call sites.
- Raise in `_upsert_session()` when the learned row is missing: also refuses a
  session whose boot-time import failed, and leaves the stale 200 on `get()`.

## Risks

A session loaded from disk whose boot import into the store failed (version
`None`, no row) is no longer served from the cache once the store answers.
The store-backed list already hides it, and the next boot re-imports it from
the JSON mirror.

## Verification

A test with two `PlanService` instances over one `FakeSessionStore`: B lists,
A deletes, then B's `get()` and B's edit raise `PlanServiceError` and the row
is not re-inserted. It fails on origin/dev. The existing stale-write and
delete tests stay green.
