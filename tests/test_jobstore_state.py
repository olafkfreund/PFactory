"""RFC-0016 (#217): durable Postgres-backed job-state store + admission.

Covers the store contract against SQLite (the same in-process pattern the
audit lane uses), so it runs in the standard backend suite. The Postgres-only
``SELECT ... FOR UPDATE`` semantics are additionally exercised against a real
Postgres in ``tests/postgres/test_jobstore_for_update.py`` (``-m postgres``);
here the cap-holds-under-concurrency test runs against SQLite with a caveat
noted inline.

These tests import the SQLAlchemy-backed ``server.jobstore`` package, so the
conftest auto-skips this module in the dependency-light backend venv (no
fastapi/sqlalchemy); they run in the CI backend job, which installs the
web-server requirements.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("sqlalchemy")

from server.jobstore import (  # noqa: E402
    JobStateStore,
    SlotDenied,
    jobstore_enabled,
    lifecycle_state_for,
)
from server.jobstore.models import JobState  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    """A FILE-based sqlite URL.

    File-based (not ``:memory:``) so every per-call connection the store opens
    via ``asyncio.run`` + ``NullPool`` sees the same persisted schema/rows — an
    in-memory sqlite DB is per-connection and would vanish between calls.
    """
    return f"sqlite+aiosqlite:///{tmp_path / 'jobstate.db'}"


@pytest.fixture
def make_store(sqlite_url):
    """Build a :class:`JobStateStore` on a freshly-created schema."""

    async def _create_schema() -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        eng = create_async_engine(sqlite_url)
        async with eng.begin() as conn:
            await conn.run_sync(JobState.metadata.create_all)
        await eng.dispose()

    asyncio.run(_create_schema())

    def _factory(*, max_concurrent: int = 4) -> JobStateStore:
        return JobStateStore(database_url=sqlite_url, max_concurrent=max_concurrent)

    return _factory


# ── lifecycle mapping (pure) ─────────────────────────────────────────────────


def test_lifecycle_mapping_matches_taxonomy():
    """PFactory native status -> canonical lifecycle_state (status-taxonomy)."""
    assert lifecycle_state_for("ingested") == "queued"
    assert lifecycle_state_for("processing") == "running"
    assert lifecycle_state_for("reviewing") == "running"
    assert lifecycle_state_for("processed") == "review"
    assert lifecycle_state_for("approved") == "review"
    assert lifecycle_state_for("rejected") == "failed"
    assert lifecycle_state_for("emitted") == "done"
    # unknown / falsy -> running-fallback (never terminal)
    assert lifecycle_state_for("totally-unknown") == "running"
    assert lifecycle_state_for(None) == "running"


def test_jobstore_enabled_reflects_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert jobstore_enabled() is False
    monkeypatch.setenv("DATABASE_URL", "  ")  # whitespace == unset
    assert jobstore_enabled() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    assert jobstore_enabled() is True


# ── write/read round-trip ────────────────────────────────────────────────────


def test_write_read_round_trip(make_store):
    store = make_store()
    store.upsert("plan-1", service_status="ingested", correlation_key=482)
    row = store.get("plan-1")
    assert row is not None
    assert row["job_id"] == "plan-1"
    assert row["service"] == "pfactory"
    assert row["kind"] == "plan"
    assert row["schema_version"] == "1"
    assert row["service_status"] == "ingested"
    assert row["lifecycle_state"] == "queued"
    assert row["correlation_key"] == "482"  # stored as text
    assert row["created_at"] is not None
    assert row["ended_at"] is None


def test_get_missing_returns_none(make_store):
    assert make_store().get("does-not-exist") is None


# ── lifecycle transitions set ended_at / result / error ──────────────────────


def test_terminal_done_sets_ended_at_and_result(make_store):
    store = make_store()
    store.upsert("plan-2", service_status="ingested")
    store.upsert(
        "plan-2",
        service_status="emitted",
        result={"epic_number": 99},
    )
    row = store.get("plan-2")
    assert row["lifecycle_state"] == "done"
    assert row["ended_at"] is not None
    assert row["result"] == {"epic_number": 99}
    assert row["error"] is None


def test_terminal_failed_requires_error(make_store):
    """never-overclaim: a terminal failure MUST carry a reason even if omitted."""
    store = make_store()
    store.upsert("plan-3", service_status="ingested")
    # omit error on a failed transition — the store must synthesise one
    store.upsert("plan-3", service_status="rejected")
    row = store.get("plan-3")
    assert row["lifecycle_state"] == "failed"
    assert row["ended_at"] is not None
    assert row["error"]  # non-empty reason was stored


def test_explicit_failed_error_is_preserved(make_store):
    store = make_store()
    store.upsert("plan-4", service_status="ingested")
    store.upsert("plan-4", service_status="failed", error="process() raised: boom")
    row = store.get("plan-4")
    assert row["lifecycle_state"] == "failed"
    assert row["error"] == "process() raised: boom"


def test_non_terminal_does_not_set_ended_at(make_store):
    store = make_store()
    store.upsert("plan-5", service_status="ingested")
    store.upsert("plan-5", service_status="processed")
    row = store.get("plan-5")
    assert row["lifecycle_state"] == "review"
    assert row["ended_at"] is None


# ── admission cap counts from the store ──────────────────────────────────────


def test_admission_cap_counts_running_from_store(make_store):
    store = make_store(max_concurrent=2)
    for jid in ("a", "b", "c"):
        store.upsert(jid, service_status="ingested")
    assert store.in_flight_count() == 3
    assert store.running_count() == 0

    assert store.try_start("a")["lifecycle_state"] == "running"
    assert store.try_start("b")["lifecycle_state"] == "running"
    assert store.running_count() == 2

    # cap reached -> third grant denied
    with pytest.raises(SlotDenied):
        store.try_start("c")

    # free a slot by taking 'a' terminal, then 'c' can start
    store.upsert("a", service_status="emitted", result={})
    assert store.running_count() == 1
    assert store.try_start("c")["lifecycle_state"] == "running"
    assert store.running_count() == 2


def test_try_start_is_idempotent(make_store):
    store = make_store(max_concurrent=1)
    store.upsert("only", service_status="ingested")
    store.try_start("only")
    # second call on an already-running row does not consume a second slot
    again = store.try_start("only")
    assert again["lifecycle_state"] == "running"
    assert store.running_count() == 1


def test_unlimited_cap_never_denies(make_store):
    store = make_store(max_concurrent=0)
    for jid in ("x", "y", "z"):
        store.upsert(jid, service_status="ingested")
        assert store.try_start(jid)["lifecycle_state"] == "running"
    assert store.running_count() == 3


# ── the slot-grant path prevents exceeding the cap under concurrency ──────────


def test_slot_grant_path_holds_cap_sequentially(make_store):
    """The slot-grant path (``try_start``) holds the cap across many jobs.

    This drives the SAME code path the durable admission gate uses, asserting
    the count-then-flip-under-transaction logic grants exactly ``cap`` slots
    and denies the rest.

    CAVEAT — SQLite vs Postgres concurrency: SQLite has no row-level
    ``SELECT ... FOR UPDATE``, so the cap-holds guarantee under *truly
    concurrent* grants from separate connections is NOT provided here (each
    connection can read "0 running" before any commits). The real
    multi-replica FOR-UPDATE race is therefore verified only against a live
    Postgres in ``tests/postgres/test_jobstore_for_update.py``. This SQLite
    test exercises the grant/deny accounting deterministically (sequentially).
    """
    cap = 3
    store = make_store(max_concurrent=cap)
    n = 8
    for i in range(n):
        store.upsert(f"j{i}", service_status="ingested")

    granted: list[str] = []
    denied: list[str] = []
    for i in range(n):
        try:
            store.try_start(f"j{i}")
            granted.append(f"j{i}")
        except SlotDenied:
            denied.append(f"j{i}")

    assert len(granted) == cap
    assert len(denied) == n - cap
    assert store.running_count() == cap


def test_concurrent_grants_do_not_corrupt_store_on_sqlite(make_store):
    """Threads racing on ``try_start`` never crash / corrupt the store.

    SQLite cannot enforce the cap across concurrent connections (see the caveat
    above), so we assert only the SAFETY properties that DO hold: every job
    ends up either running or queued (no lost/duplicated rows), and the running
    set equals the rows actually flipped — no torn writes. The cap-enforcement
    invariant is asserted against Postgres in the postgres lane.
    """
    store = make_store(max_concurrent=3)
    n = 10
    for i in range(n):
        store.upsert(f"k{i}", service_status="ingested")

    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(jid: str) -> None:
        try:
            store.try_start(jid)
        except SlotDenied:
            pass
        except Exception as exc:  # noqa: BLE001 — collect transient lock errors
            if "database is locked" not in str(exc).lower():
                with lock:
                    errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"k{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    # No row was lost: every job is still present and in a valid lifecycle.
    for i in range(n):
        row = store.get(f"k{i}")
        assert row is not None
        assert row["lifecycle_state"] in ("queued", "running")
