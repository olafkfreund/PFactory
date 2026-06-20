"""RFC-0016 (#217): FOR-UPDATE admission against a REAL Postgres.

The cap-holds invariant is unit-tested against SQLite in
``tests/test_jobstore_state.py``, but the multi-replica race the convention
targets — two control-plane replicas racing for the last concurrency slot —
is only meaningful on Postgres, where ``SELECT ... FOR UPDATE`` provides
genuine row-level serialisation. This module drives many concurrent
``try_start`` grants through real connections and asserts the cap is never
exceeded.

Marked ``@pytest.mark.postgres`` + ``@pytest.mark.slow``; the
``postgres-acceptance`` CI job opts in with ``-m postgres`` and provides the
``TEST_POSTGRES_URL`` service container. Skips cleanly otherwise.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

pytest.importorskip("asyncpg")

from server.jobstore import JobStateStore, SlotDenied  # noqa: E402
from server.jobstore.models import JobState  # noqa: E402


@pytest.fixture
def pg_schema(test_postgres_url):
    """Create the job_states table on the test Postgres; drop it after.

    Uses a unique-per-run table is overkill — the migration owns the real
    table name; here we create/drop just this model's table around the test so
    repeated runs start clean.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _setup() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            await conn.run_sync(JobState.metadata.drop_all)
            await conn.run_sync(JobState.metadata.create_all)
        await eng.dispose()

    async def _teardown() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            await conn.run_sync(JobState.metadata.drop_all)
        await eng.dispose()

    asyncio.run(_setup())
    yield
    asyncio.run(_teardown())


def test_for_update_holds_cap_under_real_concurrency(test_postgres_url, pg_schema):
    cap = 3
    store = JobStateStore(database_url=test_postgres_url, max_concurrent=cap)
    assert store.supports_row_locking is True  # real FOR UPDATE on PG

    n = 16
    for i in range(n):
        store.upsert(f"j{i}", service_status="ingested")

    granted: list[str] = []
    denied: list[str] = []
    lock = threading.Lock()

    def worker(jid: str) -> None:
        try:
            store.try_start(jid)
            with lock:
                granted.append(jid)
        except SlotDenied:
            with lock:
                denied.append(jid)

    threads = [threading.Thread(target=worker, args=(f"j{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The cap is NEVER exceeded — the core multi-replica guarantee.
    assert store.running_count() == cap
    assert len(granted) == cap
    assert len(denied) == n - cap


def test_double_start_same_job_is_safe(test_postgres_url, pg_schema):
    """Two concurrent grants of the SAME job_id consume exactly one slot."""
    store = JobStateStore(database_url=test_postgres_url, max_concurrent=5)
    store.upsert("dup", service_status="ingested")

    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            store.try_start("dup")
            with lock:
                results.append("ok")
        except SlotDenied:
            with lock:
                results.append("denied")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Idempotent: the job is running exactly once regardless of the races.
    assert store.running_count() == 1
