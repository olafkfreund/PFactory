---
status: approved
issue: 780
intent: intent/2026-09-26-780-secrets-psycopg-driver.md
---

# Spec: A required gate fails because SQLAlchemy changed its default driver

## Reproduced, both versions

A throwaway venv on each version, same code path as the tests (bare URL from
stripping `+asyncpg`) against a real Postgres:

| SQLAlchemy | bare `postgresql://` | `postgresql+psycopg2://` |
| --- | --- | --- |
| 2.0.51 (this machine) | driver `psycopg2` → connects | driver `psycopg2` → connects |
| **2.1.1 (what CI installs)** | driver `psycopg` → **`ModuleNotFoundError: No module named 'psycopg'`** | driver `psycopg2` → connects |

That is CI's exact error, and it confirms the whole story: the default moved,
the code never named a driver, and naming one fixes it on both versions.

## Design

`tests/secrets/test_p2_column_migration.py` — the three sites that build a sync
URL (lines 81, 122, 149) stop stripping the driver and name one instead. One
module-level helper so the rule lives in one place:

    _SYNC_DRIVER = "postgresql+psycopg2://"

    def _sync_url(async_url: str) -> str:
        """The sync URL for ``async_url``, with the driver NAMED (#780).

        Stripping `+asyncpg` leaves a bare `postgresql://`, whose driver is
        whatever SQLAlchemy defaults to — psycopg2 through 2.0, psycopg 3 from
        2.1, which is not installed. psycopg2-binary is in
        tests/requirements-test.txt precisely for this.
        """

Each call site becomes `sync_url = _sync_url(pg_url)`.

**Decided (intent Q1):** name the driver, do not add psycopg 3. The default is
what moved; naming it removes the class, while installing psycopg 3 would leave
the same trap for the next default change.

**Decided (intent Q2):** SQLAlchemy's floor is not pinned here. Pinning would
hide that the code relied on a default, and that floor is shared by the whole
app — a separate question from this gate.

## Alternatives rejected

- **`pytest.importorskip("psycopg")`**: the gate would go green having verified
  nothing, indistinguishable from a real pass — the shape this repo refuses
  (rule 4.10), and the intent forbids it.
- **Add `psycopg[binary]` to the test requirements**: green tomorrow, same trap
  at the next default change, and two Postgres drivers to keep straight.
- **Pin `sqlalchemy<2.2` in `apps/web-server/requirements.txt`**: freezes the
  whole app's ORM to dodge a test's assumption, and hides the real cause.
- **Also fix `crypto/__main__.py` here**: it has no sync driver in the image at
  all, so it needs a dependency or design decision, not a URL edit — filed as
  #781.

## Risks

- If psycopg2 is ever dropped from `tests/requirements-test.txt`, these tests
  fail with a clear missing-driver error rather than silently skipping. That is
  the intended direction.
- None to production: the change is test-only.

## Verification

- `tests/secrets/test_p2_column_migration.py -m secrets` passes against a real
  Postgres on **both** SQLAlchemy 2.0.51 and 2.1.1 (the CI version), run
  locally in separate venvs. Before the change, 2.1.1 fails with CI's error.
- Negative control: restore the bare `postgresql://` under 2.1.1 → the two
  named tests fail with `No module named 'psycopg'`, exactly as CI reports.
- `git grep 'replace("+asyncpg", "")' tests/` returns nothing afterwards.
- The rest of the secrets suite still passes (it does not build sync URLs).
