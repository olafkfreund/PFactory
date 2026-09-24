"""One emit per plan session across real processes, and no lost writes (#758).

`tests/postgres/test_plan_session_store.py` pins the store; these pin the fix
the way prod fails: two pods, each with its own `PlanService`, its own
in-process locks and its own memory, sharing only the Postgres row. Threads
would share `_emit_locks` and hide the bug, so the replicas are `spawn`ed
processes.

Marked ``postgres`` + ``slow``: the behaviour only exists against a real
shared database.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("asyncpg")

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from plan import service as plan_service  # noqa: E402
from plan.service import PlanService  # noqa: E402
from server.jobstore import PlanSessionStore  # noqa: E402
from server.jobstore.plan_session_models import (  # noqa: E402
    PlanSessionCounter,
    PlanSessionRow,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.slow,
    pytest.mark.skipif(
        not all(hasattr(plan_service, n) for n in ("EmitInProgressError", "StaleSessionError")),
        reason="#758 service API (EmitInProgressError, StaleSessionError) not integrated yet",
    ),
]

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""

# Generous: a spawned child re-imports the backend before it reaches the barrier.
_TIMEOUT = 60


@pytest.fixture
def pg_schema(test_postgres_url):
    """Fresh plan-session tables on the test Postgres, built from the models.

    Needs the #758 columns (`emit_lease_owner`, `emit_lease_until`, `version`)
    on `PlanSessionRow`, which is how the migration's schema reaches this test.
    """
    tables = [PlanSessionRow.__table__, PlanSessionCounter.__table__]

    async def _reset(create: bool) -> None:
        eng = create_async_engine(test_postgres_url)
        async with eng.begin() as conn:
            for table in tables:
                await conn.run_sync(table.drop, checkfirst=True)
                if create:
                    await conn.run_sync(table.create)
        await eng.dispose()

    asyncio.run(_reset(create=True))
    yield
    asyncio.run(_reset(create=False))


def _replica(url: str, tmp_path: Path, name: str) -> PlanService:
    """One pod: its own dict and locks, the shared store."""
    return PlanService(
        store_dir=tmp_path / name,
        persist=False,
        session_store=PlanSessionStore(database_url=url),
    )


class _RecordingGh:
    """A fake GitHub that appends every created issue to a file shared by all
    processes. One `O_APPEND` write per line is atomic, so lines never
    interleave. The first create of each instance (the epic, for a fresh emit)
    is slowed down to hold the race window open."""

    def __init__(self, log: Path, worker: str) -> None:
        self._log = log
        self._worker = worker
        self._n = 0

    def create_issue(self, title: str, _body: str, _labels: list[str]) -> int:
        if self._n == 0:
            time.sleep(1.0)
        fd = os.open(self._log, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            os.write(fd, f"{self._worker}\t{self._n}\t{title}\n".encode())
        finally:
            os.close(fd)
        self._n += 1
        return 1000 * (1 if self._worker == "a" else 2) + self._n

    def link_sub_issue(self, parent: int, child: int) -> None:
        pass


def _outcome(fn) -> tuple[str, str]:
    try:
        session = fn()
    except Exception as exc:  # noqa: BLE001 — the parent asserts on the type
        return type(exc).__name__, str(exc)
    return "ok", str(getattr(session, "emitted_issue_number", ""))


# Worker entry points: module level so `spawn` can import them by name.


def _emit_worker(where, name, barrier, log, results) -> None:
    url, tmp, sid = where
    svc = _replica(url, Path(tmp), name)
    gh = _RecordingGh(Path(log), name)
    barrier.wait(_TIMEOUT)
    results.put((name, *_outcome(lambda: svc.emit(sid, repo="acme/widget", dry_run=False, gh=gh))))


def _stale_worker(where, loaded, approved, results) -> None:
    """Replica B: holds a copy from before A's approval, then saves it."""
    url, tmp, sid = where
    svc = _replica(url, Path(tmp), "b")
    session = svc.get(sid)
    loaded.set()
    approved.wait(_TIMEOUT)
    # The shape of a long run (process(), a progress write) finishing on a copy
    # read before someone else's change: `_save` is where every one lands.
    session.status = "processed"
    results.put(("b", *_outcome(lambda: svc._save(session))))


def _approve_worker(where, loaded, approved, results) -> None:
    url, tmp, sid = where
    svc = _replica(url, Path(tmp), "a")
    loaded.wait(_TIMEOUT)
    results.put(("a", *_outcome(lambda: svc.approve(sid, approver="olaf"))))
    approved.set()


def _run(ctx, targets) -> dict[str, tuple[str, str]]:
    """Start the workers, collect one result each, and never leave one behind."""
    results = ctx.Queue()
    procs = [ctx.Process(target=fn, args=(*args, results)) for fn, args in targets]
    for p in procs:
        p.start()
    try:
        got = [results.get(timeout=_TIMEOUT) for _ in procs]
    finally:
        for p in procs:
            p.join(10)
            if p.is_alive():
                p.kill()
    return {name: (kind, detail) for name, kind, detail in got}


@pytest.mark.usefixtures("pg_schema")
def test_two_replicas_emit_one_session_creates_one_epic(test_postgres_url, tmp_path):
    """The #758 reproduction: both pods click emit, GitHub gets two epics."""
    seed = _replica(test_postgres_url, tmp_path, "seed")
    sid = seed.ingest_text(_PLAN, title="Refund API").session_id
    assert seed.process(sid).review.gates_passed
    seed.approve(sid, approver="olaf")

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    log = tmp_path / "github.log"
    where = (test_postgres_url, str(tmp_path), sid)
    out = _run(ctx, [(_emit_worker, (where, name, barrier, str(log))) for name in ("a", "b")])

    lines = [ln.split("\t", 2) for ln in log.read_text().splitlines()] if log.exists() else []
    writers = {worker for worker, _, _ in lines}
    epics = [ln for ln in lines if ln[1] == "0"]
    assert len(writers) == 1, f"both replicas created issues: {lines}"
    assert len(epics) == 1, f"expected one epic, got {epics}"

    (winner,) = writers
    loser = "b" if winner == "a" else "a"
    assert out[winner][0] == "ok", out
    # The loser either found the lease held, or took it after the winner
    # finished and saw the epic already recorded. Never a second emit.
    assert out[loser][0] in ("EmitInProgressError", "ok"), out
    if out[loser][0] == "ok":
        assert out[loser][1] == out[winner][1], out

    stored = _replica(test_postgres_url, tmp_path, "check").get(sid)
    assert stored.status == "emitted"
    assert str(stored.emitted_issue_number) == out[winner][1]


@pytest.mark.usefixtures("pg_schema")
def test_a_stale_replica_cannot_undo_a_newer_approval(test_postgres_url, tmp_path):
    """B read the session before A approved it; B's save must not win."""
    seed = _replica(test_postgres_url, tmp_path, "seed")
    sid = seed.ingest_text(_PLAN, title="Refund API").session_id
    assert seed.process(sid).review.gates_passed

    ctx = mp.get_context("spawn")
    loaded, approved = ctx.Event(), ctx.Event()
    args = ((test_postgres_url, str(tmp_path), sid), loaded, approved)
    out = _run(ctx, [(_stale_worker, args), (_approve_worker, args)])

    assert out["a"][0] == "ok", out
    assert out["b"][0] == "StaleSessionError", out
    assert _replica(test_postgres_url, tmp_path, "check").get(sid).status == "approved"
