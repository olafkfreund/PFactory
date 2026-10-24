---
status: approved
issue: 911
intent: intent/2026-10-24-911-invariant-companion-path.md
---

# Spec: the backend `services` invariant companion registers at startup

## Design

In `apps/web-server/server/database/alembic/versions/20260525_c6e3b2d4a8f0_encrypt_credentials.py`, change `_WEB_SERVER = Path(__file__).resolve().parents[3]` to `parents[4]`. The file sits in `alembic/versions/`, one level below `env.py`, so `parents[4]` is `apps/web-server`: the same directory `env.py` already adds. The insert then becomes the no-op its comment says it is.

## Alternatives rejected

- **Load companions by file path, or under a qualified name.** This hides the bad `sys.path` entry instead of removing it. `server/services` would still shadow `services` for any later lazy import.
- **Delete the insert.** `env.py` covers alembic runs, but the comment says direct invocation needs it. Fixing the index is smaller and keeps that path.

## Risks

- None for the schema: the revision id, `upgrade()` and `downgrade()` do not change, and the revision is already applied in every environment.

## Verification

- A new test, `apps/web-server/tests/test_invariant_companions.py`, runs in a subprocess so `sys.path` and `sys.modules` stay clean. It:
  1. loads the migration file the way alembic does;
  2. checks that `apps/web-server/server` is not on `sys.path`;
  3. runs `load_companions()` and checks that every discovered companion is loaded, `services._invariants` included.

  The test fails on `origin/dev` and passes with the fix.
- After deploy, the pod log has no "invariant companion ... failed to import" line.
