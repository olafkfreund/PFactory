---
status: approved
issue: 777
author: Olaf Krasicki-Freund
---

# Intent: a pod that migrates on boot also gets the durable job-state store without a restart

Follow-up to #774 (`intent/2026-09-26-774-store-before-migrations.md`), same
root cause.

## Problem

`PlanService.__init__` resolves the durable job-state store once
(`self._job_store = ... _resolve_job_store()`, `apps/backend/plan/service.py:619`).
The web server builds `SERVICE` when its routes import, which is before the
lifespan hook runs `init_db()` and applies `MIGRATIONS_AUTO_APPLY`. If
`job_states` is missing or not yet migrated at that moment,
`_resolve_job_store()` returns None ("job_states table is not ready ... using
the IN-MEMORY path") and the pod keeps no job store for the life of the
process. #774 fixed exactly this for the session store; it did not cover the
job store.

With no job store, three things silently change on that pod:

- **Durable job-state rows** (`_mirror`, RFC-0016 #217) are not written for
  any transition.
- **Admission** (`process_async`) uses the in-process semaphore instead of
  `_durable_admit`, so the concurrency cap no longer holds across replicas and
  queued work does not survive a restart.
- **Autoscaling.** The KEDA ScaledObject scales on
  `count(*) FROM job_states WHERE lifecycle_state='queued'`. A pod that queues
  in memory is invisible to it.

It does not happen today: `job_states` is an old migration, and the 0.6.21
pod logged "PFactory plan state is DURABLE". It would happen on a first boot
against an empty database, and on any future deploy that ships a migration
the job store depends on. Nothing but one WARNING line would show it.

Unlike the session store, `_resolve_job_store` does not cache "unavailable",
so a later call does re-check. Nothing calls it again after construction,
though.

## Proposed outcome

- A pod that applies migrations on boot has the durable job-state store from
  its first request, with no restart, just as #774 gives it the session store.
- A database that genuinely lacks `job_states` (with
  `MIGRATIONS_AUTO_APPLY=false` and no out-of-band migration) degrades as
  today, loudly.
- Covered by a test that boots against an unmigrated database.

## Affected users and systems

- PFactory web-server startup (`server/main.py` lifespan) and `plan/service.py`
  job-store resolution.
- Durable admission and the KEDA queue signal. That matters once the
  one-replica pin (factory-gitops#268) is lifted.
- Every deploy that carries a migration touching `job_states`.

## Constraints

- No behaviour change when `DATABASE_URL` is unset, or when the store
  resolves at construction (today's normal case).
- "Degrade, never fatal" stays for the job store (there is no
  require-durable switch today; adding one is out of scope).
- No per-request re-resolution.
- Reuse #774's mechanism rather than adding a parallel one.

## Open questions

Resolved 2026-09-28 (approved): 1 = (a) extend and rename, keeping an alias; 2 = confirm in the spec.

1. **Shape of the fix:**
   - (a) extend `attach_session_store_after_migrations()` to also attach the
     job store, and rename it to fit (e.g.
     `attach_stores_after_migrations()`, keeping the old name as an alias for
     one release);
   - (b) a separate `attach_job_store_after_migrations()` called next to it
     in the lifespan.

   **Recommended: (a).** It is one post-migration hook for the one root
   cause, and one place to extend if a third store ever appears.
2. **Admission already in flight at attach time?** None can be: FastAPI
   serves no requests before the lifespan completes. So attaching can simply
   assign `_job_store`, with no hand-over of in-memory state. Confirm in the
   spec.
