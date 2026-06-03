"""Pytest fixtures for P1 Postgres acceptance tests.

Tests are marked `@pytest.mark.postgres` and `@pytest.mark.slow`. Default
CI (`-m "not slow"`) excludes them. The `postgres-acceptance` CI job
opts in with `-m postgres`.
"""

from __future__ import annotations

import pytest

from tests.postgres.helpers import (
    TEST_DATABASE_URL_ENV,
    get_test_postgres_url,
    postgres_reachable,
)


@pytest.fixture(scope="session")
def test_postgres_url() -> str:
    """Resolve the test Postgres URL or skip the test.

    Skips when ``TEST_POSTGRES_URL`` is unset or the host is unreachable.
    Lets the harness land cleanly before driver-selection / Alembic
    implementation chunks land.
    """
    url = get_test_postgres_url()
    if not url:
        pytest.skip(
            f"{TEST_DATABASE_URL_ENV} not set — point this at a Postgres "
            f"(e.g. postgresql+asyncpg://user:pw@localhost:5432/pfactory_test) "
            f"to run P1 acceptance tests"
        )
    if not postgres_reachable(url):
        pytest.skip(f"Postgres at {url} is not reachable")
    return url
