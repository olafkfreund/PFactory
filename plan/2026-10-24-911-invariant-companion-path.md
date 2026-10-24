---
status: approved
issue: 911
spec: spec/2026-10-24-911-invariant-companion-path.md
---

# Plan: the backend `services` invariant companion registers at startup

## Approved decisions

- Fix the cause: the migration's `_WEB_SERVER` becomes `parents[4]` (`apps/web-server`), not `parents[3]` (`apps/web-server/server`).
- The loader does not change.
- A subprocess test proves that every companion registers after the migration module has loaded.

## Steps

1. In `apps/web-server/server/database/alembic/versions/20260525_c6e3b2d4a8f0_encrypt_credentials.py`, line 45, change `parents[3]` to `parents[4]`.

   Check: `grep -n "parents\[4\]" apps/web-server/server/database/alembic/versions/20260525_c6e3b2d4a8f0_encrypt_credentials.py`.
   Traps:
   - Do not touch the revision ids or the bodies of `upgrade()` and `downgrade()`.
   - The CodeQL alert precedent covers the module globals.
2. Add `apps/web-server/tests/test_invariant_companions.py`. It runs a subprocess with `cwd=apps/web-server` that:
   - loads the migration through `importlib.util.spec_from_file_location`;
   - asserts that no `sys.path` entry ends in `web-server/server`;
   - asserts that `load_companions()` returns every name it discovered, `services._invariants` included.

   Check: the test fails with step 1 reverted and passes with it.
   Traps:
   - Use `sys.executable`.
   - Never import the companions in-process: that would pollute `sys.modules` for the other tests.

## Tests

- `apps/backend/.venv/bin/python -m pytest apps/web-server/tests/test_invariant_companions.py`
- The pre-commit hook: the full suite and the ratchet.

## Rollback

Revert the PR. The migration is already applied, so nothing has to run.

## Deviations

- Step 2: before loading the migration, the probe puts `apps/backend` on `sys.path`, as the routes do in the app before `init_db()`. Without that, `invariants.py` inserts the backend ahead of the bad entry, and the registration check passes on the unfixed code. With it, each assertion fails on its own on `origin/dev`.
- Step 1: the pre-commit `ruff --fix` also rewrote the migration's `typing.Union`/`Sequence` imports and its three revision annotations to `X | Y` and `collections.abc.Sequence`. The values are unchanged, and the module has `from __future__ import annotations`, so alembic reads the same thing.
