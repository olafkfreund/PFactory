---
status: draft
issue: 780
author: Olaf Krasicki-Freund
---

# Intent: A required gate fails because SQLAlchemy changed its default driver

## Problem

`secrets (P2 acceptance)` — a required check — fails on every PR since
2026-09-25, including a pure Dependabot actions bump, with
`ModuleNotFoundError: No module named 'psycopg'` in two tests of
`tests/secrets/test_p2_column_migration.py`. No code changed.

Measured, not assumed:

- `tests/secrets/test_p2_column_migration.py` builds a **sync** URL by
  stripping the async driver: `url.replace("+asyncpg", "")` (lines 81, 122,
  149), leaving a bare `postgresql://…` and letting SQLAlchemy pick the
  driver.
- SQLAlchemy 2.1 changed that default from psycopg2 to psycopg 3. CI installs
  `sqlalchemy==2.1.1` (run 36242746418); this machine has 2.0.51, which is why
  it passes locally and fails there.
- `apps/web-server/requirements.txt` pins nothing: `sqlalchemy[asyncio]>=2.0.0`
  is a floor, so a new minor arrived with no commit — the #674 shape (a pin
  that looks reproducible over a part that floats).
- `tests/requirements-test.txt` ships `psycopg2-binary` for exactly this
  purpose; psycopg 3 is not installed anywhere.

So the gate does not fail because the code is wrong; it fails because the test
asked for "whatever driver SQLAlchemy defaults to today".

## Proposed outcome

- `secrets (P2 acceptance)` passes again, and the reason it broke cannot recur
  silently: the sync driver is named, not inferred.
- The tests still exercise a real Postgres migration — no import-skip. A gate
  that goes green by skipping is the failure this repo already refuses
  elsewhere (rule 4.10): it would look identical to one that verified.
- Whether SQLAlchemy's floating floor is worth pinning is answered
  deliberately, not left as the thing that bit us.

## Affected users and systems

- Everyone opening a PR (the required `secrets (P2 acceptance)` check).
- `tests/secrets/test_p2_column_migration.py`; possibly
  `apps/web-server/requirements.txt` (the SQLAlchemy floor).

## Found alongside, NOT in scope here

`apps/web-server/server/crypto/__main__.py:107` does the same thing in
**production code** — the documented key-rotation CLI
(`python -m server.crypto rotate-root`) builds `postgresql://…` and calls
`create_engine`. The shipped image installs only
`apps/web-server/requirements.txt` + `apps/backend/requirements.txt`
(`Dockerfile:272`), and neither carries psycopg2 or psycopg — so that CLI
cannot run in the image at all, on any SQLAlchemy version. That is a separate
defect with a different fix (add a sync driver to the runtime, or make the CLI
use the async one), and it gets its own issue rather than riding along here.

## Constraints

- No import-skip on a missing driver: the gate must verify or fail.
- Keep the tests running against the real Postgres service the job already
  starts.
- Do not silently widen the runtime dependency set to fix a test problem.

## Open questions

1. Name the driver explicitly (`+psycopg2`, which the test requirements
   already provide) or add psycopg 3 and let the default stand?
   Recommendation: name it. The default is what moved; naming it removes the
   class, and installing psycopg 3 leaves the same trap for the next default
   change.
2. Also pin SQLAlchemy (e.g. `>=2.0,<2.2`)? Recommendation: no — pinning it
   here hides that the code relied on a default, and the floor is shared by
   the whole app. Worth a separate look at whether the app's floors should be
   ranges, which is a wider question than this gate.
