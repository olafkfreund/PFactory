---
status: draft
issue: 781
spec: spec/2026-09-29-781-rotate-cli-driver.md
---

# Plan: The key-rotation runbook cannot run in the image that ships it

Approved decisions (from the spec):

- `server/crypto/__main__.py`: replace the `.replace("+asyncpg", "")` stripping
  with a `_sync_url()` rewrite built on `make_url`, naming
  `postgresql+psycopg` for any postgres URL and `sqlite` for any sqlite one,
  leaving other schemes alone.
- `apps/web-server/requirements.txt`: add `psycopg[binary]>=3.2` beside
  `asyncpg`, with a comment saying it serves the rotation runbook (the only
  sync-engine caller) and costs ~10.4MB.
- Tests: a `docker`-marked test that runs the runbook **in the built image** and
  asserts it reaches its own "DATABASE_URL not set" guard with no
  `ModuleNotFoundError`; plus unit tests for `_sync_url` in the fast suite.
- Not touched: `rotation.py`'s sync-`Engine` signature.

Measured already, and quoted rather than re-derived: sqlalchemy 2.1.1 in the
image's dep set; psycopg2 and psycopg both absent; bare `postgresql://` resolves
to `psycopg`; `psycopg[binary]` 3.3.6 connects with the named URL; 2.0.51 and
2.1.1 both know the dialect; the image's WORKDIR is `apps/web-server` with the
venv on PATH, so `python -m server.crypto …` is the whole command.

## Steps

1. `apps/web-server/server/crypto/__main__.py`: add `_sync_url()` (module level,
   docstring naming #781 and why the driver is named), use it in
   `_cmd_rotate_root`, drop the string-stripping. Keep the DATABASE_URL presence
   guard ahead of it so a missing URL still exits 2 with its own message.
   → verify by step 4's unit tests.
2. `apps/web-server/requirements.txt`: add the dependency + comment.
   → verify by step 5 (the image test) and `pip install` resolving it.
3. Handle a malformed URL: wrap the `make_url` call so the CLI prints
   `DATABASE_URL is not a valid database URL: <reason>` and exits 2 rather than
   raising. → verify by step 4's malformed case.
4. `tests/secrets/test_p2_rotation.py` (the existing rotation test home): add
   unit tests for `_sync_url` — `+asyncpg` → `+psycopg`, bare `postgresql://` →
   `+psycopg`, `+psycopg2` → `+psycopg`, `+aiosqlite` → `sqlite`, an unrelated
   scheme unchanged, and a malformed value exiting 2 with the clear message.
5. `tests/docker/test_p0_runtime.py`: add
   `test_the_rotation_runbook_runs_in_the_image` — `docker_run(built_image,
   "python", "-m", "server.crypto", "rotate-root", "--new-kms-key-id", "probe")`
   with no DATABASE_URL in the container env; assert exit 2, "DATABASE_URL not
   set" in the output, and `"ModuleNotFoundError" not in` it.
6. Reproduction, inverted (not committed): in `/tmp/claude-1000/imgdeps` (the
   image's dep set, now with psycopg), re-run the command that failed at the
   start → it must reach "DATABASE_URL not set". Record both outputs.
7. Negative controls (not committed): (a) uninstall psycopg from that venv → the
   command fails with `ModuleNotFoundError` again, proving step 2 is load-bearing;
   (b) restore the old string-stripping → the same failure on SQLAlchemy 2.1,
   proving step 1 is too. Restore both.
8. Build the image once and run the docker group, so step 5 is exercised rather
   than left to CI.

## Tests

    apps/backend/.venv/bin/pytest tests/secrets/ -m secrets -q
    docker build -t pfactory:781 .
    apps/backend/.venv/bin/pytest tests/docker/test_p0_runtime.py -m docker -q
    apps/backend/.venv/bin/pytest tests/ -q -k "rotation or crypto"

Expected: all pass. The full backend suite runs in the pre-commit hook. The
`secrets (P2 acceptance)` and `docker (P0 acceptance)` jobs are the CI gates.

## Rollback

Revert the commit: the CLI returns to stripping the driver (and to failing in
the image), and the image drops ~10.4MB. No state, no schema, no data touched —
rotation was never able to run, so nothing has been rotated by this path.
