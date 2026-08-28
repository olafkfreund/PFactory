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
    lease_heartbeat_interval,
    lease_ttl_seconds,
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


# ── lease / reclaim: a killed owner must not leak its slot (#300) ────────────
#
# The bug these cover: try_start flips a row to `running`, and the ONLY things
# that flip it back are (a) a terminal transition inside process() and (b) the
# `except` in _durable_admit. A SIGKILL runs NEITHER, so the row stayed
# `running` forever and its admission slot was gone for good — the cap decayed
# toward zero across the whole fleet. Every test below fails without the lease.


def _strand(store, job_id: str) -> None:
    """Simulate an owner that was SIGKILLed: running, lease in the past.

    A backdated lease is exactly the state a killed pod leaves behind — the row
    is `running` and nothing will ever renew it again.
    """
    store.upsert(job_id, service_status="ingested")
    store.try_start(job_id)
    assert store.heartbeat(job_id, ttl_seconds=-1) is True


def test_expired_lease_row_is_reclaimed(make_store):
    store = make_store()
    _strand(store, "dead")

    assert store.reclaim_expired() == 1

    row = store.get("dead")
    assert row["lifecycle_state"] == "failed"  # terminal, off `running`
    assert row["ended_at"] is not None
    assert "lease expired" in row["error"]  # never-overclaim: carries a reason
    assert store.running_count() == 0  # the slot is back


def test_live_lease_row_is_not_reclaimed(make_store):
    """A healthy, heartbeating plan MUST survive — reclaiming it is worse than
    the leak it fixes."""
    store = make_store()
    store.upsert("alive", service_status="ingested")
    store.try_start("alive")

    assert store.reclaim_expired() == 0
    assert store.heartbeat("alive") is True  # owner renews
    assert store.reclaim_expired() == 0

    assert store.get("alive")["lifecycle_state"] == "running"
    assert store.running_count() == 1


def test_admission_cap_recovers_after_a_strand(make_store):
    """The user-visible bug: a killed pod's row wedges admission forever."""
    store = make_store(max_concurrent=1)
    _strand(store, "dead")

    # Before the fix the slot is gone for good and this is unreachable.
    store.upsert("next", service_status="ingested")
    store.try_start("next")  # reclaims `dead` inline, then grants

    assert store.get("dead")["lifecycle_state"] == "failed"
    assert store.get("next")["lifecycle_state"] == "running"
    assert store.running_count() == 1  # cap still honoured, not exceeded


def test_reclaim_is_idempotent_and_single_shot_under_concurrency(make_store):
    """Two replicas reclaiming the same row: exactly ONE reclaims it.

    The atomic conditional UPDATE (no read-then-write) is what makes this hold:
    the loser's `lifecycle_state='running'` predicate no longer matches once the
    winner has committed.
    """
    store = make_store()
    _strand(store, "dead")

    counts: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            n = store.reclaim_expired()
        except Exception as exc:  # noqa: BLE001 — sqlite writer contention
            if "database is locked" not in str(exc).lower():
                raise
            return
        with lock:
            counts.append(n)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(counts) == 1, f"double-reclaimed: {counts}"
    assert store.get("dead")["lifecycle_state"] == "failed"

    # Idempotent: a settled row is never reclaimed again.
    assert store.reclaim_expired() == 0


def test_heartbeat_only_renews_a_running_row(make_store):
    store = make_store()
    store.upsert("q", service_status="ingested")
    assert store.heartbeat("q") is False  # queued, not running -> nothing to renew
    assert store.heartbeat("nonexistent") is False

    store.try_start("q")
    assert store.heartbeat("q") is True
    store.upsert("q", service_status="emitted")
    assert store.heartbeat("q") is False  # terminal -> the owner is done


def test_lease_ttl_and_interval_are_configurable(monkeypatch):
    monkeypatch.delenv("PFACTORY_PLAN_LEASE_TTL_SECONDS", raising=False)
    assert lease_ttl_seconds() == 600
    assert lease_heartbeat_interval() == 150.0
    monkeypatch.setenv("PFACTORY_PLAN_LEASE_TTL_SECONDS", "60")
    assert lease_ttl_seconds() == 60
    assert lease_heartbeat_interval() == 15.0
    monkeypatch.setenv("PFACTORY_PLAN_LEASE_TTL_SECONDS", "8")
    assert lease_heartbeat_interval() == 5.0  # floor
    monkeypatch.setenv("PFACTORY_PLAN_LEASE_TTL_SECONDS", "not-an-int")
    assert lease_ttl_seconds() == 600  # bad value -> default, never raises


def test_discarded_is_terminal_and_frees_its_slot():
    """The bug that wedged the planner for six days.

    discard() writes status="discarded". With no entry in the map, the
    running-fallback turned that TERMINAL status into `running`, so the row
    kept its admission slot -- and because it also had no lease,
    reclaim_expired (which only touches rows WITH an expired lease) could
    never free it. 43 slots leaked until the cap was full and no plan could
    start at all.
    """
    from server.jobstore.lifecycle import TERMINAL_LIFECYCLE, is_terminal

    assert lifecycle_state_for("discarded") == "failed"
    assert lifecycle_state_for("discarded") in TERMINAL_LIFECYCLE
    assert is_terminal("discarded")
    # Not "done": a discarded session did not succeed, and the board reads
    # `done` as success.
    assert lifecycle_state_for("discarded") != "done"


def test_every_status_the_service_writes_has_an_explicit_mapping():
    """Drift guard: the running-fallback must never catch our OWN vocabulary.

    The fallback exists for unrecognised third-party statuses, where assuming
    "still running" is the safe read. For a status PFactory itself writes it is
    the opposite of safe -- a terminal status silently classified as running
    holds an admission slot forever, which is exactly how `discarded` wedged
    the planner.

    Parsed from the service source rather than hand-listed, so adding a new
    `session.status = "..."` without a mapping fails HERE instead of leaking
    slots in production for six days.
    """
    import re
    from pathlib import Path

    from server.jobstore.lifecycle import _NATIVE_TO_LIFECYCLE

    service = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "apps" / "backend" / "plan" / "service.py"
        if candidate.is_file():
            service = candidate
            break
    if service is None:  # pragma: no cover - layout change
        pytest.skip("plan/service.py not found from this checkout")

    written = set(re.findall(r'session\.status\s*=\s*"([a-z_]+)"', service.read_text()))
    assert written, "found no status writes -- the regex has gone stale"
    # "ingested" is set at construction rather than assignment; it is mapped.
    missing = sorted(written - set(_NATIVE_TO_LIFECYCLE))
    assert not missing, (
        f"statuses PFactory writes but does not map: {missing}. "
        "Unmapped statuses fall back to 'running' and leak an admission slot."
    )


# ── leaseless `running` rows (#360, Factory#1004) ────────────────────────────
#
# The reclaim above only touches rows WITH an expired lease, because an expired
# lease is what proves the owner died. That leaves a row at `running` with
# lease_expires_at IS NULL permanently unreclaimable -- and only try_start
# stamps a lease, so any path recording `running` without it leaks an admission
# slot for good. 43 slots leaked over six days exactly this way.
#
# A missing lease is NOT evidence of death, the same way a missing heartbeat is
# not: a row sits leaselessly for the instant between insert and try_start. So
# the reclaim requires a SECOND independent signal -- a terminal service status
# -- and the tests below pin both halves. The fail-closed half is the one that
# matters most: reclaiming a live row kills a running plan, which is not
# recoverable, where a leaked slot is.


def _strand_leaseless(sqlite_url: str, job_id: str, service_status: str) -> None:
    """Put a row at `running` with NO lease and the given service status.

    Written directly because no store method can now produce this state -- the
    status-map hole that did has been closed. The state itself remains
    reachable by any future path that records `running` without try_start,
    which is precisely what must stay reclaimable.
    """

    async def _go() -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        eng = create_async_engine(sqlite_url)
        async with eng.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE job_states SET lifecycle_state='running', "
                    "lease_expires_at=NULL, service_status=:s WHERE job_id=:j"
                ),
                {"s": service_status, "j": job_id},
            )
        await eng.dispose()

    asyncio.run(_go())


def _set_service_status(sqlite_url: str, job_id: str, service_status: str) -> None:
    """Change only the service status, leaving lease and lifecycle untouched."""

    async def _go() -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        eng = create_async_engine(sqlite_url)
        async with eng.begin() as conn:
            await conn.execute(
                text("UPDATE job_states SET service_status=:s WHERE job_id=:j"),
                {"s": service_status, "j": job_id},
            )
        await eng.dispose()

    asyncio.run(_go())


def test_leaseless_running_row_with_terminal_status_is_reclaimed(
    make_store, sqlite_url
):
    """The 43-slot leak: terminal work still holding `running`, with no lease."""
    store = make_store()
    store.upsert("leaked", service_status="ingested")
    _strand_leaseless(sqlite_url, "leaked", "discarded")
    assert store.running_count() == 1  # the slot is held

    assert store.reclaim_expired() == 1

    row = store.get("leaked")
    assert row["lifecycle_state"] == "failed"
    assert row["ended_at"] is not None
    assert store.running_count() == 0  # slot returned


def test_leaseless_running_row_with_live_status_is_left_alone(
    make_store, sqlite_url
):
    """Fail-closed. `processing` is not terminal, so the missing lease alone
    must not reclaim -- this is the row that is mid-try_start, and killing it
    would end a running plan."""
    store = make_store()
    store.upsert("busy", service_status="ingested")
    _strand_leaseless(sqlite_url, "busy", "processing")

    assert store.reclaim_expired() == 0
    assert store.get("busy")["lifecycle_state"] == "running"
    assert store.running_count() == 1


def test_leaseless_running_row_with_unknown_status_is_left_alone(
    make_store, sqlite_url
):
    """Fail-closed on a status we cannot classify.

    An unknown status maps to `running` by the taxonomy fallback, so it is
    absent from TERMINAL_NATIVE_STATUSES and must not be reclaimed. A status
    nobody has taught us about is not evidence that the work finished.
    """
    store = make_store()
    store.upsert("mystery", service_status="ingested")
    _strand_leaseless(sqlite_url, "mystery", "some-future-status")

    assert store.reclaim_expired() == 0
    assert store.get("mystery")["lifecycle_state"] == "running"


def test_leaseless_reclaim_is_idempotent(make_store, sqlite_url):
    store = make_store()
    store.upsert("twice", service_status="ingested")
    _strand_leaseless(sqlite_url, "twice", "emitted")

    assert store.reclaim_expired() == 1
    assert store.reclaim_expired() == 0  # nothing left to reclaim


def test_leaseless_reclaim_records_the_status_it_found(make_store, sqlite_url):
    """Releasing the slot must not invent a failure.

    `emitted` is terminal AND successful. Writing `failed` across every
    reclaimed row would turn a plan that emitted into a permanent, false
    record of failure -- the row is being released, not condemned.
    """
    store = make_store()
    store.upsert("shipped", service_status="ingested")
    _strand_leaseless(sqlite_url, "shipped", "emitted")

    assert store.reclaim_expired() == 1
    assert store.get("shipped")["lifecycle_state"] == "done"

    store.upsert("binned", service_status="ingested")
    _strand_leaseless(sqlite_url, "binned", "discarded")

    assert store.reclaim_expired() == 1
    assert store.get("binned")["lifecycle_state"] == "failed"


def test_leased_row_is_not_touched_by_the_leaseless_path(make_store, sqlite_url):
    """Scope guard. A row holding a LIVE lease belongs to the lease path, which
    releases it when the lease expires. Widening this reclaim to leased rows
    would let it act on work whose owner is still alive and renewing."""
    store = make_store()
    store.upsert("held", service_status="ingested")
    store.try_start("held")  # stamps a live lease
    _set_service_status(sqlite_url, "held", "emitted")

    assert store.reclaim_expired() == 0
    assert store.get("held")["lifecycle_state"] == "running"


def test_terminal_native_statuses_tracks_the_map():
    """Derived, not hand-listed: a status added to the map cannot go missing.

    `discarded` is the one that caused the leak, so it is named explicitly.
    """
    from server.jobstore.lifecycle import (
        _NATIVE_TO_LIFECYCLE,
        TERMINAL_LIFECYCLE,
        TERMINAL_NATIVE_STATUSES,
    )

    assert "discarded" in TERMINAL_NATIVE_STATUSES
    assert "processing" not in TERMINAL_NATIVE_STATUSES
    assert TERMINAL_NATIVE_STATUSES == {
        n for n, c in _NATIVE_TO_LIFECYCLE.items() if c in TERMINAL_LIFECYCLE
    }
