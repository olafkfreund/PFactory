---
status: draft
issue: 808
intent: intent/2026-10-22-808-ingested-not-queued.md
---

# Spec: an abandoned ingested session is discarded after a TTL

## Decision

Owner's decision B (2026-10-22): give abandoned ingested sessions a terminal
path in PFactory. Option A (change the KEDA metric) is rejected here; it is
factory-gitops#273's work.

## Design

An age-based sweep that discards stale ingested sessions through the existing
`discard()` path.

- `PlanService.discard_stale_ingested(max_age_seconds, *, now=None) -> list[str]`
  in `apps/backend/plan/service.py`. It reads every session
  (`_all_sessions()`, so the shared store on a multi-replica deploy), and for
  each with `status == "ingested"` and `created_at` older than the cutoff calls
  `self.discard(sid, actor="system:stale-ingest-sweep", reason=...)`. The
  reason names the age and #808. It returns the discarded ids.
- `discard()` already does everything a terminal exit needs: status
  `discarded` → lifecycle `failed` in `job_states` (via `_save` → `_mirror`),
  the completion event, the audit fields (`discard.from_status`), and hiding
  the session from the default board list (#798). Nothing new is mirrored.
- A per-session failure (for example `StaleSessionError` when another replica
  discarded it first, or an unparseable `created_at`) is logged and skipped;
  the sweep goes on.
- A web-server loop `background/stale_ingest_sweep.py`, shaped like the
  liveness loop: run the sweep in a worker thread every hour, log and swallow
  iteration errors, stop on cancel. Started and cancelled in the `main.py`
  lifespan.
- Setting `APP_STALE_INGEST_TTL_HOURS` (`STALE_INGEST_TTL_HOURS`, default
  `168` = 7 days, `0` disables the loop).

### Why a sweep, and not a hook on delete/discard

Measured on origin/dev: the explicit exits already end the row. `discard()`
maps to `failed`, and `DELETE` (#798/#821) accepts only `discarded`/`rejected`
sessions, so their rows are terminal before the delete runs. A hook there
would change nothing. The production rows are sessions nobody touched again,
and only a time-based path reaches those. So the sweep alone covers both
cases: deleted sessions (already covered) and abandoned ones (new).

## Alternatives rejected

- **A — change the KEDA metric:** out of scope; factory-gitops#273.
- **Hook delete/discard to end the row:** already the behaviour (above);
  adds no coverage.
- **Remap `ingested` away from `queued`:** the taxonomy defines `queued` as
  "not started", which is correct; a remap would misreport the state to every
  consumer.
- **Mirror `queued` only at admission:** PFactory has no admission queue for
  ingested sessions (processing is caller-driven), and the session would still
  never reach a terminal state.
- **A direct `job_states` UPDATE by age:** leaves the session store saying
  `ingested` while the row says `failed`, and skips the audit and completion
  event that `discard()` writes.
- **Sweep on `list_sessions`:** puts writes and webhooks on a read path.

## Risks

- A user who ingests and comes back after more than 7 days finds the session
  discarded and has to ingest again. The TTL is configurable, and the discard
  record says why.
- With several replicas each runs the sweep. `discard()` is idempotent and a
  lost race raises `StaleSessionError`, which is skipped. The work is one list
  read per hour.
- On first deploy the sweep discards the two production rows from 2026-09-04.
  That is the intended outcome and goes through the normal release, not a
  manual production write.

## Verification

- Unit tests in `tests/test_plan_discard.py`, with an in-memory job store:
  an ingested session older than the TTL is discarded by the sweep and its
  `job_states` row is `failed`; a fresh ingested session and an older session
  in any other status are untouched; the sweep with nothing stale returns `[]`;
  a session with an unparseable `created_at` is skipped, not fatal.
  The stale-session test fails on origin/dev (the method does not exist).
- The loop is a thin timer like the liveness loop and has no test of its own.
- `ruff`, `scripts/ratchet_lint.py`, then targeted and full `pytest tests/`.
