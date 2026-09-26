---
status: approved
issue: 780
spec: spec/2026-09-26-780-secrets-psycopg-driver.md
---

# Plan: A required gate fails because SQLAlchemy changed its default driver

Approved decisions (from the spec):

- The three sync-URL sites in `tests/secrets/test_p2_column_migration.py`
  (lines 81, 122, 149) stop stripping the driver and name it, via one
  module-level `_sync_url()` helper carrying the reason (#780).
- Driver named `+psycopg2`; `psycopg2-binary` is already in
  `tests/requirements-test.txt` for exactly this. psycopg 3 is NOT added.
- SQLAlchemy's floor is not pinned here.
- No `importorskip`: the gate verifies or fails.
- Test-only change; `crypto/__main__.py` is #781.

Reproduced before writing this (throwaway venvs, real Postgres): under
`sqlalchemy==2.1.1` the bare URL resolves to driver `psycopg` and raises
`ModuleNotFoundError: No module named 'psycopg'` — CI's error — while
`+psycopg2` connects. Under 2.0.51 both connect.

## Steps

1. `tests/secrets/test_p2_column_migration.py`: add `_SYNC_DRIVER` +
   `_sync_url(async_url)` beside the other module helpers, with the comment
   explaining that a bare URL takes SQLAlchemy's moving default. Replace the
   three `url.replace("+asyncpg", "")` sites with `_sync_url(...)`.
   → verify `git grep -n 'replace("+asyncpg", "")' tests/` is empty.
2. Run the file against Postgres on the CURRENT SQLAlchemy (2.0.51): all tests
   pass — proves the change does not regress today's version.
3. Run the same file on SQLAlchemy **2.1.1** (the CI version) in the throwaway
   venv at `/tmp/claude-1000/sa21`, with the repo on `sys.path`: the two named
   tests pass. This is the one that matters — it is what CI runs.
4. Negative control (not committed): restore the bare `postgresql://` in
   `_sync_url`, re-run step 3 → the two tests fail with
   `No module named 'psycopg'`, matching CI exactly. Restore.
5. Whole secrets suite on 2.0.51 (`-m secrets`): unchanged, nothing else builds
   a sync URL.

## Tests

    # a throwaway Postgres (container on :55433) provides TEST_POSTGRES_URL
    TEST_POSTGRES_URL=postgresql+asyncpg://postgres:pf780@localhost:55433/pf780 \
      apps/backend/.venv/bin/pytest tests/secrets/ -m secrets -q
    TEST_POSTGRES_URL=... /tmp/claude-1000/sa21/bin/python -m pytest \
      tests/secrets/test_p2_column_migration.py -m secrets -q

Expected: green on both. The full backend suite runs in the pre-commit hook;
the real gate is CI's `secrets (P2 acceptance)` on the PR, which is the job
this fixes.

## Rollback

Revert the commit. Test-only: the sites return to relying on SQLAlchemy's
default, and the gate fails again on 2.1.x.
