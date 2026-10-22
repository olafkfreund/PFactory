---
status: approved
issue: 808
spec: spec/2026-10-22-808-ingested-not-queued.md
---

# Plan: an abandoned ingested session is discarded after a TTL

Decisions carried from the approved spec:

- **Owner's decision B:** a terminal path for abandoned ingested sessions, in
  PFactory. Option A (the KEDA metric) is factory-gitops#273's and is not
  touched here. No production write.
- **The fix:** an age-based sweep that calls the existing `discard()` for every
  `ingested` session whose `created_at` is older than the TTL. No hook on
  delete/discard: those exits already end the row (`discarded` → `failed`;
  `DELETE` accepts only terminal sessions).
- `discard()` does the mirroring, completion event and audit fields; nothing
  new is written to `job_states` directly.
- Per-session failures are logged and skipped.
- Hourly loop in the web server; `APP_STALE_INGEST_TTL_HOURS`, default 168,
  `0` disables.
- **Rejected:** changing the KEDA metric; hooking delete/discard; remapping
  `ingested`; mirroring `queued` only at admission; a direct `job_states`
  UPDATE; sweeping on `list_sessions`.

## Steps

1. `apps/backend/plan/service.py`: add
   `PlanService.discard_stale_ingested(max_age_seconds, *, now=None) -> list[str]`
   next to `discard()`, as the spec describes.
2. `apps/web-server/server/config.py`: add
   `STALE_INGEST_TTL_HOURS: int = Field(168, ge=0)`.
   `apps/web-server/server/background/stale_ingest_sweep.py`: a
   `stale_ingest_sweep_loop(ttl_seconds, interval_seconds=3600)` that runs
   `SERVICE.discard_stale_ingested` in a thread each interval, logs the count,
   swallows iteration errors. `apps/web-server/server/main.py`: start it in the
   lifespan when the TTL is above 0; cancel it on shutdown.
3. `tests/test_plan_discard.py`: tests from the spec's Verification
   (stale discarded and mirrored as `discarded`, fresh and non-ingested
   untouched, nothing stale returns `[]`, a bad `created_at` is skipped).

## Tests

`ruff check` / `ruff format --check` on touched files,
`python scripts/ratchet_lint.py` if present, `pytest tests/test_plan_discard.py`,
then the full `pytest tests/` via the pre-commit hook. The stale test must fail
on origin/dev.

## Deviations

- Steps 1 and 2 were done by codex. Step 1 was then tightened in-session: the
  sweep re-reads each session before discarding it, so a session another
  replica started processing since the list is left alone, and a naive
  `created_at` (`TypeError`) is skipped like a bad one. In step 2 a redundant
  `except CancelledError: raise` was removed.
- Step 3 was written in-session; its three new tests fail on origin/dev.
- CI follow-up: the loop casts the lazy `SERVICE` (typed `object` by the
  module `__getattr__`) to `PlanService` for the mypy ratchet; the tests save
  their edits through `_save()` and assert membership, so they hold on the
  Postgres job where the session store is shared.

## Rollback

Set `APP_STALE_INGEST_TTL_HOURS=0`, or revert the PR. Sessions already
discarded stay discarded (they can be re-ingested); no schema change.
