---
status: draft
issue: 781
author: Olaf Krasicki-Freund
---

# Intent: The key-rotation runbook cannot run in the image that ships it

## Problem

`python -m server.crypto rotate-root` is the documented way to rotate the KMS
root key — the recovery path for a compromised or expiring key. It cannot run in
the shipped image.

`apps/web-server/server/crypto/__main__.py:107` builds a sync URL by stripping
the async driver and lets SQLAlchemy pick the replacement:

    sync_url = db_url.replace("+asyncpg", "").replace("+aiosqlite", "")
    engine = create_engine(sync_url)

The chart supplies `postgresql+asyncpg://…`
(`charts/pfactory/templates/postgres-bundled.yaml:22`), so that leaves a bare
`postgresql://`, whose driver is whatever SQLAlchemy defaults to.

Reproduced in the image's exact dependency set — the two requirements files
`Dockerfile:272` installs, and nothing else:

| | |
| --- | --- |
| sqlalchemy | 2.1.1 |
| asyncpg | present |
| psycopg2 | **missing** |
| psycopg | **missing** |
| default driver for `postgresql://` | `psycopg` |

Running the documented command in that environment:

    ModuleNotFoundError: No module named 'psycopg'

raised from `create_engine`, before the rotation touches a row. On SQLAlchemy
2.0 the same line fails naming `psycopg2` instead, so this is not a regression
from the 2.1 default change (#780) — that only changed which module name
appears. The runbook has never been runnable in the image.

Nothing catches it: the crypto tests drive `rotate_root()` and the migration
helpers directly, and no test invokes the CLI entry point, so "the runbook runs
in the image we ship" is asserted nowhere.

## Proposed outcome

- `python -m server.crypto rotate-root` runs in the shipped image and reaches
  its own argument validation and database work rather than an import error.
- The sync driver is **named**, not inferred, so a future SQLAlchemy default
  cannot move it again (the #780 lesson, applied where it actually bites).
- A test asserts the entry point is reachable in the image's dependency set, so
  the claim stops resting on nobody having tried it.
- SQLite deployments keep working unchanged (`sqlite://` needs no third-party
  driver).

## Affected users and systems

- Anyone rotating the KMS root key on a Postgres deployment — the incident path.
- `apps/web-server/server/crypto/__main__.py`, possibly
  `apps/web-server/requirements.txt` (a sync driver in the runtime), tests.
- Not `rotate_root()` itself, whose sync-engine signature is deliberate ("a
  one-shot ops task, not part of a hot request path").

## Constraints

- The image is deliberately minimal and security-scanned; a new runtime
  dependency needs to earn its place, and its size and CVE surface are part of
  the decision.
- No change to `rotation.py`'s design: it takes a sync `Engine` on purpose.
- The fix must work on both SQLAlchemy 2.0 and 2.1, since the floor is
  unpinned (`sqlalchemy[asyncio]>=2.0.0`).
- Rotation is an incident path: prefer a dependency that is present when needed
  over machinery that must be assembled under pressure.

## Open questions

1. **Which driver, and where.** (a) add `psycopg[binary]` to the runtime and
   name `+psycopg`; (b) add `psycopg2-binary` and name `+psycopg2` (what the
   test requirements already use); (c) keep the image lean and run the runbook
   from a maintenance image that installs a driver. Recommendation: (a) —
   psycopg 3 is the actively developed driver and is what SQLAlchemy 2.1 would
   have chosen anyway, and naming it removes the guessing. (c) adds a moving
   part to an incident.
2. **How far does the test go?** A unit test that the module imports and its
   parser runs is cheap but proves little; asserting the CLI reaches database
   work inside the built image (the `docker (P0 acceptance)` job already builds
   one) is the honest version. Recommendation: the latter, as a `docker`-marked
   test that runs the command with no `DATABASE_URL` and expects its own
   "DATABASE_URL not set" exit, not an import error.
