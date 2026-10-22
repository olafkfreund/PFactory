"""#901: the stale-ingest sweep ends orphaned ``job_states`` rows.

#808's sweep walks ``plan_sessions``, so a ``queued`` row whose session was
lost had nothing to walk and stayed queued forever (KEDA counted it). Runs on a
SQLite ``JobStateStore``; the conftest skips it where SQLAlchemy is missing.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
for _p in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")
pytest.importorskip("pydantic")

from plan.service import PlanService  # noqa: E402
from server.jobstore import JobStateStore  # noqa: E402
from server.jobstore.models import JobState  # noqa: E402

_DAY = 86400
_PROBE = "# probe\n\n## Acceptance Criteria\n- AC#1: GET /healthz returns 200\n"


@pytest.fixture
def store(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'jobstate.db'}"

    async def _create_schema() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(JobState.metadata.create_all)
        await eng.dispose()

    asyncio.run(_create_schema())
    s = JobStateStore(database_url=url)
    yield s
    s.close()


def _cutoff(days_from_now: float) -> datetime:
    """The sweep's ``now - TTL``: positive puts every row created now before it."""
    return datetime.now(UTC) + timedelta(days=days_from_now)


def test_an_orphan_older_than_the_ttl_is_ended(store):
    """MUTATION GUARD (#901): this row stayed `queued` forever."""
    store.upsert("orphan", service_status="ingested")

    assert store.end_orphaned_queued(_cutoff(1), set()) == 1

    row = store.get("orphan")
    assert row["lifecycle_state"] == "failed"  # lifecycle_state_for("discarded")
    assert row["service_status"] == "discarded"
    assert row["ended_at"]
    assert "orphaned session" in row["error"]


def test_a_fresh_orphan_is_untouched(store):
    store.upsert("fresh", service_status="ingested")

    assert store.end_orphaned_queued(_cutoff(-1), set()) == 0
    assert store.get("fresh")["lifecycle_state"] == "queued"


def test_a_row_with_a_live_session_is_untouched(store):
    store.upsert("live", service_status="ingested")

    assert store.end_orphaned_queued(_cutoff(1), {"live"}) == 0
    assert store.get("live")["lifecycle_state"] == "queued"


@pytest.mark.parametrize("status", ["processing", "processed", "emitted", "rejected"])
def test_non_queued_rows_are_untouched(store, status):
    before = store.upsert("other", service_status=status)

    assert store.end_orphaned_queued(_cutoff(1), set()) == 0
    after = store.get("other")
    assert after["service_status"] == status
    assert after["lifecycle_state"] == before["lifecycle_state"]
    assert after["ended_at"] == before["ended_at"]


def test_the_service_sweep_ends_orphans_but_not_its_own_rows(store):
    svc = PlanService(job_store=store)
    processed = svc.ingest_text(_PROBE, title="has a session").session_id
    session = svc.get(processed)
    session.status = "processed"
    svc._save(session)
    store.upsert(processed, service_status="ingested")  # queued, but the session lives
    store.upsert("lost-before-755", service_status="ingested")

    svc.discard_stale_ingested(7 * _DAY, now=datetime.now(UTC) + timedelta(days=8))

    assert store.get("lost-before-755")["service_status"] == "discarded"
    assert store.get(processed)["lifecycle_state"] == "queued"
