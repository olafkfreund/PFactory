"""Plan sessions are shared across replicas, not cached per process (#755).

The prod incident, as a test: four pods, a discard applied on one, three still
answering `ingested` until a rollout restart. `PlanService` loaded every
session into a per-process dict at startup and read only from it, so a write on
one replica was invisible to the others; `self._seq = len(self._sessions)`
could also mint the same id twice.

Marked ``postgres`` + ``slow``: the multi-replica behaviour these pin only
exists against a real shared database.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

pytest.importorskip("asyncpg")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from plan.service import PlanService  # noqa: E402
from server.jobstore import PlanSessionStore  # noqa: E402
from server.jobstore.plan_session_models import (  # noqa: E402
    PlanSessionCounter,
    PlanSessionRow,
)

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT
"""


@pytest.fixture
def pg_schema(test_postgres_url):
    """Create the plan_sessions table on the test Postgres; drop it after.

    Only this model's table, so the fixture cannot disturb job_states rows a
    neighbouring test owns.
    """
    tables = [PlanSessionRow.__table__, PlanSessionCounter.__table__]

    async def _setup() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            for table in tables:
                await conn.run_sync(table.drop, checkfirst=True)
                await conn.run_sync(table.create, checkfirst=True)
        await eng.dispose()

    async def _teardown() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            for table in tables:
                await conn.run_sync(table.drop, checkfirst=True)
        await eng.dispose()

    asyncio.run(_setup())
    yield
    asyncio.run(_teardown())


def _store(url: str) -> PlanSessionStore:
    return PlanSessionStore(database_url=url)


def _replica(url: str, tmp_path: Path, name: str) -> PlanService:
    """A PlanService standing in for one pod: its own dict, one shared store."""
    return PlanService(store_dir=tmp_path / name, persist=False, session_store=_store(url))


@pytest.mark.usefixtures("pg_schema")
def test_a_write_on_one_replica_is_visible_to_another(test_postgres_url, tmp_path):
    """The #755 reproduction: discard on pod A, read on pod B, no restart."""
    pod_a = _replica(test_postgres_url, tmp_path, "a")
    pod_b = _replica(test_postgres_url, tmp_path, "b")

    sid = pod_a.ingest_text(_PLAN, title="Refund API").session_id
    # B has never seen this session: it was created after B started.
    assert pod_b.get(sid).status == "ingested"

    pod_a.discard(sid, actor="olaf", reason="teardown")

    assert pod_b.get(sid).status == "discarded"
    summaries = {s["session_id"]: s["status"] for s in pod_b.list_sessions()}
    assert summaries[sid] == "discarded"


@pytest.mark.usefixtures("pg_schema")
def test_two_replicas_never_mint_the_same_session_id(test_postgres_url, tmp_path, caplog):
    """`self._seq = len(self._sessions)` was per process, so ids collided."""
    pod_a = _replica(test_postgres_url, tmp_path, "a")
    pod_b = _replica(test_postgres_url, tmp_path, "b")

    with caplog.at_level("WARNING", logger="plan.service"):
        ids = {
            pod_a.ingest_text(_PLAN, title="one").session_id,
            pod_b.ingest_text(_PLAN, title="two").session_id,
            pod_a.ingest_text(_PLAN, title="three").session_id,
            pod_b.ingest_text(_PLAN, title="four").session_id,
        }

    assert len(ids) == 4
    # The service falls back to its per-process counter on a store failure,
    # which is how the broken first cut passed this test while still colliding.
    assert not [r for r in caplog.records if "allocation failed" in r.getMessage()]


@pytest.mark.usefixtures("pg_schema")
def test_on_disk_sessions_are_imported_once(test_postgres_url, tmp_path):
    """A deployment that gains DATABASE_URL must not strand its PVC sessions."""
    store_dir = tmp_path / "shared-disk"
    # A pod with no store, persisting to disk — the pre-#755 world.
    legacy = PlanService(store_dir=store_dir, persist=True)
    sid = legacy.ingest_text(_PLAN, title="Refund API").session_id

    # The same disk, now with a shared store: the import runs at startup.
    upgraded = PlanService(
        store_dir=store_dir, persist=True, session_store=_store(test_postgres_url)
    )
    assert upgraded.get(sid).session_id == sid

    # A second boot must not duplicate or resurrect anything.
    again = PlanService(store_dir=store_dir, persist=True, session_store=_store(test_postgres_url))
    assert len([s for s in again.list_sessions() if s["session_id"] == sid]) == 1


@pytest.mark.usefixtures("pg_schema")
def test_a_store_read_failure_degrades_to_the_cached_copy(test_postgres_url, tmp_path):
    """A DB hiccup must not fail a read the process can already answer."""
    pod = _replica(test_postgres_url, tmp_path, "a")
    sid = pod.ingest_text(_PLAN, title="Refund API").session_id

    class _Broken:
        def get(self, _sid):
            raise RuntimeError("connection reset")

        def list_payloads(self, **_kw):
            raise RuntimeError("connection reset")

    pod._session_store = _Broken()

    assert pod.get(sid).session_id == sid  # served from the local cache
    assert any(s["session_id"] == sid for s in pod.list_sessions())


@pytest.mark.usefixtures("pg_schema")
def test_next_seq_actually_allocates_and_never_repeats(test_postgres_url):
    """The first cut used `max(seq)+1 FOR UPDATE`, which Postgres refuses with
    an aggregate — so allocation raised, the service silently fell back to its
    per-process counter, and the collision #755 reported was still there. This
    calls the primitive directly: a warning-swallowed failure looks identical
    to success from the outside."""
    store = _store(test_postgres_url)

    handed_out = [store.next_seq() for _ in range(5)]

    assert handed_out == sorted(handed_out)
    assert len(set(handed_out)) == 5


@pytest.mark.usefixtures("pg_schema")
def test_concurrent_allocation_hands_out_distinct_numbers(test_postgres_url):
    """Two replicas allocating at the same moment must not collide."""
    stores = [_store(test_postgres_url), _store(test_postgres_url)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(lambda i: stores[i % 2].next_seq(), range(12)))

    assert len(set(got)) == 12


@pytest.mark.usefixtures("pg_schema")
def test_the_counter_clears_sessions_imported_after_the_migration(test_postgres_url):
    """The migration seeds the counter at 0; the import brings in higher ids.

    Reviewed on #760: `_highest_existing_seq()` fed only the INSERT values, and
    the conflict path (the only path that runs once the migration has seeded
    the row) ignored it — so the first allocation on an upgraded deployment
    returned 1 and collided with the imported `001-...` session.
    """

    async def _seed() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            # what the migration does
            await conn.execute(text("INSERT INTO plan_session_seq (id, value) VALUES (1, 0)"))
            # what the one-shot JSON import does
            for n in (1, 2, 3):
                await conn.execute(
                    text(
                        "INSERT INTO plan_sessions "
                        "(session_id, tenant_id, seq, schema_version, payload) "
                        "VALUES (:sid, 'default', :seq, '1', '{}')"
                    ),
                    {"sid": f"00{n}-imported", "seq": n},
                )
        await eng.dispose()

    asyncio.run(_seed())

    assert _store(test_postgres_url).next_seq() == 4


# ── #758: emit lease and compare-and-set writes ─────────────────────────


@pytest.mark.usefixtures("pg_schema")
def test_emit_lease_is_exclusive_until_released_or_expired(test_postgres_url):
    """One emit per session across replicas: the lease is the cross-pod lock."""
    store = _store(test_postgres_url)
    assert store.acquire_emit_lease("001-x", "pod-a", 60) is False  # no row yet

    assert store.upsert("001-x", payload="{}", seq=1, tenant_id=None, expected_version=0) == 1
    assert store.acquire_emit_lease("001-x", "pod-a", 60) is True
    assert store.acquire_emit_lease("001-x", "pod-b", 60) is False

    store.release_emit_lease("001-x", "pod-b")  # not the owner: a no-op
    assert store.acquire_emit_lease("001-x", "pod-b", 60) is False

    store.release_emit_lease("001-x", "pod-a")
    assert store.acquire_emit_lease("001-x", "pod-b", 60) is True

    # A crashed holder never releases; its lease lapses instead.
    async def _expire() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE plan_sessions SET emit_lease_until = now() - interval '1 second' "
                    "WHERE session_id = :sid"
                ),
                {"sid": "001-x"},
            )
        await eng.dispose()

    asyncio.run(_expire())
    assert store.acquire_emit_lease("001-x", "pod-c", 60) is True


@pytest.mark.usefixtures("pg_schema")
def test_upsert_is_a_compare_and_set_on_version(test_postgres_url):
    """A replica holding an older copy must not overwrite a newer write."""
    store = _store(test_postgres_url)
    assert store.get("001-x") is None

    assert store.upsert("001-x", payload="v1", seq=1, tenant_id=None, expected_version=0) == 1
    assert store.upsert("001-x", payload="dup", seq=1, tenant_id=None, expected_version=0) is None
    assert store.get("001-x") == ("v1", 1)

    assert store.upsert("001-x", payload="v2", seq=1, tenant_id="t", expected_version=1) == 2
    assert store.upsert("001-x", payload="stale", seq=1, tenant_id="t", expected_version=1) is None
    assert store.get("001-x") == ("v2", 2)

    assert store.upsert("002-y", payload="x", seq=2, tenant_id=None, expected_version=3) is None
    assert store.get("002-y") is None


# ── the store is picked up after boot (#774) ──────────────────────────


def test_a_service_built_before_the_migration_picks_the_store_up(
    test_postgres_url, tmp_path, monkeypatch, caplog
):
    """The prod reproduction: routes import SERVICE at module scope, so the
    store resolves BEFORE the lifespan applies migrations. That pod then served
    its whole life per-process — `plan_sessions` empty, the on-disk sessions
    never imported, the #758 lease and #766 compare-and-set inactive — until a
    manual `rollout restart` (#774, measured 0.5s apart in prod)."""
    from plan import service as svc
    from sqlalchemy.ext.asyncio import create_async_engine

    tables = [PlanSessionRow.__table__, PlanSessionCounter.__table__]

    async def _drop() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            for table in tables:
                await conn.run_sync(table.drop, checkfirst=True)
        await eng.dispose()

    async def _migrate() -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            for table in tables:
                await conn.run_sync(table.create, checkfirst=True)
        await eng.dispose()

    asyncio.run(_drop())
    monkeypatch.setenv("DATABASE_URL", test_postgres_url)
    monkeypatch.setattr(svc, "_SESSION_STORE_CACHE", {})
    monkeypatch.setattr(svc, "_SESSION_STORE_RETRY_AFTER", {})

    # A pod whose on-disk store already holds a session (the PVC case).
    store_dir = tmp_path / "disk"
    seeded = svc.PlanService(store_dir=store_dir, persist=True)
    sid = seeded.ingest_text(_PLAN, title="before the migration").session_id

    pod = svc.PlanService(store_dir=store_dir, persist=True)
    assert pod._session_store is None  # the table does not exist yet

    asyncio.run(_migrate())  # the lifespan runs `alembic upgrade head`
    # Past the back-off, without sleeping through it.
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        svc.time,
        "monotonic",
        lambda: real_monotonic() + svc._STORE_RETRY_COOLDOWN_SECONDS + 1,
    )

    with caplog.at_level("INFO", logger="plan.service"):
        store = pod._session_store  # same instance, no restart

    assert store is not None, "the pod never picked the store up"
    assert any("became SHARED after boot" in r.getMessage() for r in caplog.records)
    # and the on-disk session it was holding reached the shared table
    assert sid in store.session_ids()
    asyncio.run(_drop())
