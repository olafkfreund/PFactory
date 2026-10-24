---
status: approved
issue: 911
author: olafkfreund
---

# Intent: the backend `services` invariant companion registers at startup

## Problem

At every start the web server logs `ModuleNotFoundError: No module named 'services._invariants'`, so the invariants in `apps/backend/services/_invariants.py` never register. `/api/ready` and the invariant timer never check them.

Cause, traced in the running pod: the alembic migration `20260525_c6e3b2d4a8f0_encrypt_credentials.py` computes `_WEB_SERVER` as `parents[3]`. It is one directory deeper than `env.py`, whose `parents[3]` it copied, so it puts `apps/web-server/server` first on `sys.path`. After `init_db()` runs the migrations, `services` resolves to `server/services`, which has no `_invariants`.

## Proposed outcome

All 32 companions register at startup, and no error is logged.

## Affected users and systems

- `apps/web-server/server/database/alembic/versions/20260525_c6e3b2d4a8f0_encrypt_credentials.py`.
- `/api/ready` and the invariant timer in every PFactory pod.

## Constraints

- No migration revision or schema change; only the path the module computes.
- The loader in `server/background/invariants.py` stays as it is.

## Open questions

None.
