---
status: approved
issue: 781
intent: intent/2026-09-29-781-rotate-cli-driver.md
---

# Spec: The key-rotation runbook cannot run in the image that ships it

## Facts established (all measured, not inferred)

- In the image's dependency set (the two requirements files `Dockerfile:272`
  installs): sqlalchemy 2.1.1, asyncpg present, **psycopg2 and psycopg both
  missing**, and a bare `postgresql://` resolves to `psycopg`.
- Running the documented command there fails with
  `ModuleNotFoundError: No module named 'psycopg'` from inside `create_engine`.
- `psycopg[binary]` 3.3.6 installs into that set and connects with an
  explicitly named `postgresql+psycopg://` URL. Cost: **~10.4MB**
  (psycopg 2.1M + psycopg-binary 8.3M).
- Both SQLAlchemy majors know the dialect: 2.0.51 and 2.1.1 each resolve
  `postgresql+psycopg://` to driver `psycopg`, so the unpinned floor is safe.
- The image already does the awkward parts: `WORKDIR` is
  `/home/projects/MagesticAI/apps/web-server` and the venv is on `PATH`
  (`Dockerfile:300,313`), so `python -m server.crypto …` is the whole command.

## Design

### 1. Name the driver, and normalise every shape (intent Q1)

`apps/web-server/server/crypto/__main__.py` replaces the string-stripping with
a URL rewrite:

    from sqlalchemy.engine.url import make_url

    def _sync_url(db_url: str) -> str:
        """The sync-driver URL for an async DATABASE_URL (#781).

        The driver is NAMED, never inferred: a bare `postgresql://` takes
        whatever SQLAlchemy defaults to — psycopg2 through 2.0, psycopg 3 from
        2.1 — which is how this runbook came to fail at `create_engine` in the
        shipped image.
        """
        url = make_url(db_url)
        if url.get_backend_name() == "postgresql":
            return str(url.set(drivername="postgresql+psycopg"))
        if url.get_backend_name() == "sqlite":
            return str(url.set(drivername="sqlite"))
        return db_url

Rewriting rather than stripping also fixes the shapes the old code mishandled:
a URL already carrying `+psycopg2` (whose driver is absent) and a bare
`postgresql://` both become `+psycopg`. SQLite keeps its stdlib driver, so
those deployments are untouched.

### 2. The driver ships in the runtime

`apps/web-server/requirements.txt` gains `psycopg[binary]>=3.2` beside
`asyncpg`, with a comment stating it exists for the rotation runbook (the only
sync-engine caller) and what it costs.

### 3. A test that runs the runbook in the built image (intent Q2)

`tests/docker/test_p0_runtime.py` gains a `docker`-marked test using the
existing `built_image` fixture and `docker_run` helper:

    docker run <image> python -m server.crypto rotate-root --new-kms-key-id probe

with **no** `DATABASE_URL`. The CLI's own guard should answer — exit 2 with
"DATABASE_URL not set" — and the test asserts the output contains no
`ModuleNotFoundError`. That distinguishes "reached its own logic" from "died at
import", which is exactly the defect.

A unit test is added too, for the rewrite itself (`_sync_url` over the shapes
above), since that runs in the fast suite where the docker test does not.

## Alternatives rejected

- **`psycopg2-binary`**: works, and the test requirements already use it, but it
  is the legacy driver and SQLAlchemy 2.1 no longer defaults to it. Naming
  psycopg 3 puts the runtime on the same driver SQLAlchemy would choose.
- **Keep the image lean; run the runbook from a maintenance image**: adds a step
  to be assembled during an incident, which is when this path is used.
- **Make `rotate_root()` async over asyncpg**: its sync-`Engine` signature is
  deliberate ("a one-shot ops task, not part of a hot request path"), and it
  would spread through `rotation.py` and its tests for no gain here.
- **Import-guard with a friendly message** ("install psycopg first"): turns a
  broken runbook into a documented broken runbook.

## Risks

- ~10.4MB and one more package in a deliberately minimal, Trivy-scanned image.
  psycopg 3 is actively maintained, which is the trade being made: a slightly
  larger image for a recovery path that works.
- The Trivy gate (`test_trivy_no_high_critical`) now scans psycopg too. If it
  ever reports a fixable HIGH there, the pin moves — the same contract every
  other runtime dependency has.
- `make_url` raises on a malformed `DATABASE_URL` where the old code would have
  limped to a driver error. The CLI already validates presence; the spec's
  unit test covers a malformed value so the failure is a clear message rather
  than a traceback.

## Verification

- **The reproduction, inverted:** the same command that failed with
  `ModuleNotFoundError` in the image's dependency set now reaches its own
  "DATABASE_URL not set" guard. Run before and after in that venv.
- **In the built image** (`docker`-marked, the acceptance job builds it): the
  new test asserts exit 2, the guard's message, and no `ModuleNotFoundError`.
- **Unit:** `_sync_url` maps `+asyncpg` → `+psycopg`, bare `postgresql://` →
  `+psycopg`, `+psycopg2` → `+psycopg`, `+aiosqlite` → `sqlite`, and leaves an
  unrelated scheme alone; a malformed URL fails with a clear message.
- **Negative control:** revert the requirements line → the in-image test fails
  with `ModuleNotFoundError` again; revert the rewrite → it fails the same way
  on SQLAlchemy 2.1.
- Existing suites green: `tests/secrets/ -m secrets`, `tests/docker -m docker`
  (the runtime group), and the full backend suite via the hook.
