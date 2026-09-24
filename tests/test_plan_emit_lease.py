"""One live emit per plan session across replicas (#758).

The in-process lock only covers one pod. With a shared store, a live emit also
takes a lease on the session row; a second replica is refused, a store it
cannot reach refuses too (fail closed), and a replica that gets the lease after
another finished sees the emitted session and creates nothing.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan import service as plan_service  # noqa: E402
from plan.service import EmitInProgressError, PlanService  # noqa: E402
from tests.fake_session_store import FakeSessionStore  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


class _Gh:
    def __init__(self) -> None:
        self.titles: list[str] = []
        self._n = 100

    def create_issue(self, title, body, labels):
        self._n += 1
        self.titles.append(title)
        return self._n

    def link_sub_issue(self, parent, child):
        pass


def _approved(store: FakeSessionStore) -> tuple[PlanService, str]:
    svc = PlanService(session_store=store)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    assert svc.process(sid).review.gates_passed
    svc.approve(sid, approver="olaf")
    return svc, sid


def test_a_lease_held_by_another_replica_refuses_the_emit():
    store = FakeSessionStore()
    svc, sid = _approved(store)
    store.lease[sid] = "other-pod:1:abcd"
    gh = _Gh()

    with pytest.raises(EmitInProgressError, match="another replica"):
        svc.emit(sid, repo="acme/widget", dry_run=False, gh=gh)
    with pytest.raises(EmitInProgressError):
        svc.emit_contract(sid, dry_run=False, http=object())

    assert gh.titles == []
    assert svc.get(sid).emitted_issue_number is None
    assert store.lease[sid] == "other-pod:1:abcd"  # never released someone else's


def test_a_live_emit_takes_and_releases_the_lease():
    store = FakeSessionStore()
    svc, sid = _approved(store)
    gh = _Gh()

    out = svc.emit(sid, repo="acme/widget", dry_run=False, gh=gh)

    assert out.status == "emitted"
    assert len(store.lease_calls) == 1
    _, owner, ttl = store.lease_calls[0]
    assert ttl == 1800
    assert store.released == [(sid, owner)]
    assert store.lease == {}


def test_an_emit_already_finished_elsewhere_creates_nothing():
    store = FakeSessionStore()
    svc, sid = _approved(store)
    # Replica B finished first and wrote the emitted session to the store.
    other = PlanService(session_store=store)
    other.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh())

    def _no_emit(*_a, **_kw):
        raise AssertionError("emitted again after another replica finished")

    svc._emit = _no_emit  # type: ignore[method-assign]
    out = svc.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh())

    assert out.status == "emitted"
    assert out.emitted_issue_number == 101
    assert store.lease == {}


def test_a_session_missing_from_the_store_is_stored_before_the_lease():
    store = FakeSessionStore()
    svc, sid = _approved(store)
    del store.rows[sid]  # e.g. the store was down when it was written
    svc._sessions[sid]._store_version = None

    out = svc.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh())

    assert out.status == "emitted"
    assert len(store.lease_calls) == 1
    assert sid in store.rows


def test_a_dry_run_takes_no_lease():
    store = FakeSessionStore()
    svc, sid = _approved(store)
    store.lease[sid] = "other-pod:1:abcd"  # a live emit is running elsewhere

    svc.emit(sid, repo="acme/widget", dry_run=True)
    svc.emit_contract(sid, dry_run=True)

    assert store.lease_calls == []


def test_an_unreachable_store_refuses_the_emit():
    store = FakeSessionStore()
    svc, sid = _approved(store)
    store.acquire_error = ConnectionError("db down at 10.0.0.5")
    gh = _Gh()

    with pytest.raises(EmitInProgressError, match="cannot confirm") as info:
        svc.emit(sid, repo="acme/widget", dry_run=False, gh=gh)

    assert gh.titles == []
    assert "10.0.0.5" not in info.value.client_message  # no inner error leaks
    # The in-process lock was released too: a later emit is not stuck.
    store.acquire_error = None
    assert svc.emit(sid, repo="acme/widget", dry_run=False, gh=gh).status == "emitted"


def test_a_failed_release_is_logged_not_raised(caplog):
    store = FakeSessionStore()
    svc, sid = _approved(store)
    store.release_error = ConnectionError("db down")

    with caplog.at_level(logging.WARNING, logger="plan.service"):
        out = svc.emit(sid, repo="acme/widget", dry_run=False, gh=_Gh())

    assert out.status == "emitted"
    assert any("emit lease" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 1800), ("600", 600), ("0", 1800), ("-5", 1800), ("soon", 1800), ("", 1800)],
)
def test_lease_ttl_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("PFACTORY_EMIT_LEASE_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("PFACTORY_EMIT_LEASE_TTL_SECONDS", raw)
    assert plan_service._emit_lease_ttl() == expected
