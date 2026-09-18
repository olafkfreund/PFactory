"""A live emit survives being killed mid-run and never blocks the event loop (#725).

The field report: a 34-issue emit held the event loop, the liveness probe
killed the pod after 27 issues, and the session still showed the previous dry
run — so a re-run would have duplicated all 27. These tests pin the three fixes:
numbers are saved as each issue appears, the routes run the emit in a worker
thread, and one session cannot run two emits at once.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.emit.github_emitter import emit_to_github  # noqa: E402
from plan.service import PlanService, PlanServiceError  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


class _Killed(BaseException):
    """Stands in for SIGKILL: the emitter's `except Exception` must not catch it."""


class _Gh:
    """Records created issues; optionally dies on the Nth create or waits on a gate."""

    def __init__(self, *, die_on: int | None = None, gate: threading.Event | None = None):
        self.titles: list[str] = []
        self._n = 100
        self._die_on = die_on
        self._gate = gate
        self.started = threading.Event()

    def create_issue(self, title, body, labels):
        self.started.set()
        if self._gate is not None:
            self._gate.wait(5)
        if self._die_on is not None and len(self.titles) + 1 == self._die_on:
            raise _Killed
        self._n += 1
        self.titles.append(title)
        return self._n

    def link_sub_issue(self, parent, child):
        pass


def _approved(svc: PlanService) -> str:
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    assert svc.process(sid).review.gates_passed
    svc.approve(sid, approver="olaf")
    return sid


# ── emitter: on_progress ──────────────────────────────────────────────────────


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-x",
        epic_title="Epic",
        children=[ChildIssue(key=k, title=f"Child {k}") for k in ("A", "B", "C")],
    )


def test_progress_reports_epic_then_each_child_including_reused():
    calls: list[tuple[int, dict[str, int]]] = []
    emit_to_github(
        _epic(),
        repo="o/r",
        gh=_Gh(),
        dry_run=False,
        existing_child_numbers={"C": 7},
        on_progress=lambda e, c: calls.append((e, dict(c))),
    )
    # First report already carries the reused child C, not just the ones passed.
    assert calls == [
        (101, {"C": 7}),
        (101, {"C": 7, "A": 102}),
        (101, {"C": 7, "A": 102, "B": 103}),
    ]


def test_a_failing_progress_callback_does_not_stop_creation():
    gh = _Gh()

    def boom(*_):
        raise RuntimeError("store down")

    result = emit_to_github(_epic(), repo="o/r", gh=gh, dry_run=False, on_progress=boom)
    assert len(gh.titles) == 4
    assert not result.errors


def test_dry_run_never_reports_progress():
    calls = []
    emit_to_github(_epic(), repo="o/r", dry_run=True, on_progress=lambda *a: calls.append(a))
    assert calls == []


# ── service: kill mid-emit, restart, resume ───────────────────────────────────


def test_killed_emit_is_recorded_and_resumes_without_duplicates(tmp_path):
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = _approved(svc)
    children = len(svc.get(sid).epic.children)
    assert children >= 4

    first = _Gh(die_on=5)  # epic + 3 children, then killed on the 4th child
    with pytest.raises(_Killed):
        svc.emit(sid, repo="acme/widget", dry_run=False, gh=first)

    # A brand-new service over the same store is the restarted pod.
    restarted = PlanService(store_dir=tmp_path, persist=True)
    session = restarted.get(sid)
    assert session.status == "approved"
    assert session.emit_result["dry_run"] is False
    assert session.emitted_issue_number == 101
    assert len(session.emit_result["child_numbers"]) == 3

    second = _Gh()
    second._n = 200
    out = restarted.emit(sid, repo="acme/widget", dry_run=False, gh=second)

    assert out.status == "emitted"
    assert len(second.titles) == children - 3  # only the missing ones
    assert len(first.titles) + len(second.titles) == 1 + children  # each issue once
    assert len(out.emit_result["child_numbers"]) == children


def test_emit_lock_is_released_after_a_killed_emit(tmp_path):
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = _approved(svc)
    with pytest.raises(_Killed):
        svc.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh(die_on=2))
    assert svc.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh()).status == "emitted"


def test_a_second_emit_of_the_same_session_is_refused_while_one_runs():
    svc = PlanService()
    sid = _approved(svc)
    gate = threading.Event()
    gh = _Gh(gate=gate)
    worker = threading.Thread(
        target=svc.emit, args=(sid,), kwargs={"repo": "acme/widget", "dry_run": False, "gh": gh}
    )
    worker.start()
    assert gh.started.wait(5)

    with pytest.raises(PlanServiceError, match="already running"):
        svc.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh())
    with pytest.raises(PlanServiceError, match="already running"):
        svc.emit_contract(sid, dry_run=True)

    gate.set()
    worker.join(5)
    assert svc.get(sid).status == "emitted"
    assert sum(t == svc.get(sid).epic.epic_title for t in gh.titles) == 1  # one epic


# ── routes: the event loop keeps serving while an emit runs ───────────────────


class _Request:
    headers: dict[str, str] = {}


async def _ticks_while(coro) -> int:
    ticks = 0
    task = asyncio.ensure_future(coro)
    while not task.done():
        await asyncio.sleep(0.01)
        ticks += 1
    await task
    return ticks


@pytest.fixture
def slow_service(monkeypatch):
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    svc.process(sid)

    def slow(session_id, **_):
        time.sleep(0.5)  # a blocking emit, like dozens of `gh` subprocess calls
        return svc.get(session_id)

    monkeypatch.setattr(svc, "emit", slow)
    monkeypatch.setattr(svc, "emit_contract", slow)
    monkeypatch.setattr(pp, "SERVICE", svc)

    async def no_docs(*_):
        return None

    monkeypatch.setattr(pp, "_load_docs_connections", no_docs)
    return sid


def test_emit_route_does_not_block_the_event_loop(slow_service):
    body = pp.EmitBody(repo="acme/widget", dry_run=True)
    ticks = asyncio.run(_ticks_while(pp.emit(slow_service, body, _Request(), db=None)))
    assert ticks > 20


def test_emit_contract_route_does_not_block_the_event_loop(slow_service):
    body = pp.EmitContractBody(dry_run=True)
    ticks = asyncio.run(_ticks_while(pp.emit_contract(slow_service, body)))
    assert ticks > 20
