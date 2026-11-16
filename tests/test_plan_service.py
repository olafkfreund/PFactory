"""Tests for PlanService — the full-pipeline orchestrator (#20 backend)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.service import (  # noqa: E402
    PlanService,
    PlanServiceError,
    PlanSession,
    StaleSessionError,
)
from tests.fake_session_store import FakeSessionStore  # noqa: E402

_SOFTWARE_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


def test_ingest_then_process_runs_full_pipeline():
    svc = PlanService()
    session = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API")
    assert session.status == "ingested"
    assert session.plan.criteria  # parsed ACs

    processed = svc.process(session.session_id)
    assert processed.status == "processed"
    assert processed.plan.target_kind == "software"
    assert processed.plan.plan_type == "software-service"
    kinds = {c.kind for c in processed.epic.children}
    assert "feature" in kinds and "testing" in kinds and "cicd" in kinds
    assert {a.kind for a in processed.artifacts} == {"testing", "cicd"}
    assert processed.review is not None
    assert processed.epic.validate_dependencies() == []


def test_approve_then_emit_dry_run():
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    review = svc.process(sid).review
    assert review.gates_passed  # healthy plan passes

    svc.approve(sid, approver="olaf")
    session = svc.get(sid)
    assert session.status == "approved"
    assert session.review.ready_to_emit()

    emitted = svc.emit(sid, repo="olafkfreund/Demo", dry_run=True)
    assert emitted.emit_result["dry_run"] is True
    assert len(emitted.emit_result["planned"]) >= 1  # epic + children planned


def test_emit_refused_before_approval_when_not_dry_run():
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    svc.process(sid)
    emitted = svc.emit(sid, repo="olafkfreund/Demo", dry_run=False)
    # ungoverned (no approval) → emitter refuses, records an error, no status change
    assert emitted.emit_result["errors"]
    assert emitted.status != "emitted"


def test_list_and_unknown_session():
    svc = PlanService()
    svc.ingest_text(_SOFTWARE_PLAN, title="Refund API")
    assert len(svc.list_sessions()) == 1
    assert svc.list_sessions()[0]["status"] == "ingested"
    with pytest.raises(PlanServiceError):
        svc.get("999-nope")


def test_cannot_approve_before_process():
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    with pytest.raises(PlanServiceError):
        svc.approve(sid, approver="olaf")


# ── the cockpit needs WHY a plan is blocked, not just THAT it is (CFactory#245) ──


def _blocked_session():
    from plan.models import Criterion, NormalizedPlan
    from plan.review.models import Finding, LensScore, PlanReview
    from plan.service import PlanSession

    plan = NormalizedPlan(
        plan_id="027",
        title="VAT quote endpoint",
        description="d",
        source_format="markdown",
        criteria=[Criterion(id="AC#1", text="x")],
    )
    review = PlanReview(
        plan_id="027",
        threshold=0.75,
        lenses=[
            LensScore(
                lens="security",
                score=0.70,
                findings=[
                    Finding(title="No authentication/authorization criteria", severity="medium")
                ],
            ),
            LensScore(lens="clarity", score=1.0),
        ],
    ).recompute()
    return PlanSession(session_id="027", plan=plan, review=review)


def test_summary_carries_the_per_lens_verdict():
    """`gates_passed` alone cannot disable a button with a reason attached."""
    out = _blocked_session().summary()
    assert out["gates_passed"] is False

    review = out["review"]
    assert review["threshold"] == 0.75
    lenses = {ls["lens"]: ls for ls in review["lenses"]}
    assert lenses["security"]["score"] == 0.70
    assert lenses["security"]["findings"][0]["title"] == "No authentication/authorization criteria"
    assert lenses["clarity"]["findings"] == []


def test_summary_aggregate_is_carried_but_is_not_the_verdict():
    """The aggregate clears the threshold while the plan is still blocked.

    This is the exact shape that confused a real approval: 'lens security scored
    0.70, below the 0.75 threshold ... the 0.94 aggregate is not the test'. A
    consumer reading the aggregate as the verdict would disagree with the server.
    """
    review = _blocked_session().summary()["review"]
    assert review["aggregate_score"] > review["threshold"]
    assert review["gates_passed"] is False


def test_summary_review_is_none_before_the_gates_run():
    """An un-reviewed session must serialise unchanged for existing consumers."""
    from plan.models import NormalizedPlan
    from plan.service import PlanSession

    plan = NormalizedPlan(plan_id="028", title="t", description="d", source_format="markdown")
    out = PlanSession(session_id="028", plan=plan).summary()
    assert out["review"] is None
    assert out["gates_passed"] is None


# ── multi-replica guard (#755) ────────────────────────────────────────


def test_multi_replica_without_a_shared_store_is_an_error(monkeypatch, caplog):
    """The WARNING about the in-memory path was logged all through the prod
    incident and nobody saw it, so >1 replica with no store is an ERROR."""
    monkeypatch.setenv("PFACTORY_REPLICA_COUNT", "4")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PFACTORY_REQUIRE_SHARED_STORE", raising=False)
    # Any create_app() earlier in the run defers the guard process-wide (#774).
    monkeypatch.setattr("plan.service._DEFER_REPLICA_GUARD", False)

    with caplog.at_level("ERROR", logger="plan.service"):
        PlanService()

    assert any("PFACTORY_REPLICA_COUNT=4" in r.getMessage() for r in caplog.records)
    assert any("#755" in r.getMessage() for r in caplog.records)


def test_require_shared_store_refuses_to_start(monkeypatch):
    """So the KEDA pin can be lifted against a guarantee, not a hope."""
    monkeypatch.setenv("PFACTORY_REPLICA_COUNT", "2")
    monkeypatch.setenv("PFACTORY_REQUIRE_SHARED_STORE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Any create_app() earlier in the run defers the guard process-wide (#774).
    monkeypatch.setattr("plan.service._DEFER_REPLICA_GUARD", False)

    with pytest.raises(RuntimeError, match="#755"):
        PlanService()


def test_single_replica_without_a_store_stays_quiet(monkeypatch, caplog):
    monkeypatch.setenv("PFACTORY_REPLICA_COUNT", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with caplog.at_level("ERROR", logger="plan.service"):
        PlanService()

    assert not [r for r in caplog.records if "#755" in r.getMessage()]


def test_an_unusable_store_is_resolved_once_not_per_service(monkeypatch):
    """Each store owns a loop, a thread and a pool (#755).

    The first cut returned None on an unready store WITHOUT closing it, so a
    process that builds many PlanServices — the full suite against Postgres —
    leaked all three per construction. Resolution must happen once and be
    remembered.
    """
    from plan import service as svc

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://nobody@127.0.0.1:1/none")
    monkeypatch.setattr(svc, "_SESSION_STORE_CACHE", {})
    monkeypatch.setattr(svc, "_SESSION_STORE_UNAVAILABLE", set())

    built = []

    class _Unready:
        def __init__(self, *_a, **_kw):
            built.append(self)
            self.closed = False

        def is_ready(self):
            return False

        def close(self):
            self.closed = True

    monkeypatch.setitem(
        __import__("sys").modules,
        "server.jobstore",
        type("M", (), {"PlanSessionStore": _Unready})(),
    )

    assert svc._resolve_session_store() is None
    assert svc._resolve_session_store() is None
    assert svc._resolve_session_store() is None

    assert len(built) == 1, "the store was rebuilt per call"
    assert built[0].closed is True, "the unusable store was not closed"


def test_ingest_bytes_stamps_originator_and_agent_identity():
    session = PlanService(persist=False).ingest_bytes(
        _SOFTWARE_PLAN.encode(),
        filename="plan.md",
        originator="u-1",
        agent_identity="agent:pfactory/k-1",
    )
    assert session.originator == "u-1"
    assert session.agent_identity == "agent:pfactory/k-1"


def test_attribution_round_trips_and_old_payloads_load_as_none():
    session = PlanService(persist=False).ingest_text(
        _SOFTWARE_PLAN, originator="u-1", agent_identity="agent:pfactory/k-1"
    )
    reloaded = PlanSession.model_validate_json(session.model_dump_json())
    assert (reloaded.originator, reloaded.agent_identity) == ("u-1", "agent:pfactory/k-1")

    payload = json.loads(session.model_dump_json())
    del payload["originator"], payload["agent_identity"]
    old = PlanSession.model_validate(payload)
    assert old.originator is None and old.agent_identity is None


# ── downstream feedback (#998) ─────────────────────────────────────────────


def _evt(service, status, key, **extra):
    return {
        "correlation_key": key,
        "service": service,
        "status": status,
        "updated_at": "2026-11-16T00:00:00Z",
        **extra,
    }


def _keyed(svc, key, repo="o/r", tenant="default"):
    s = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API")
    s.correlation_key, s.repo, s.tenant_id = key, repo, tenant
    return s


def test_record_downstream_stores_on_the_one_matching_session(tmp_path):
    svc = PlanService(store_dir=tmp_path)
    s = _keyed(svc, "42")
    assert svc.record_downstream(_evt("aifactory", "failed", "42", halt_reason="boom"))
    assert svc.get(s.session_id).downstream.handbacks[0]["reason"] == "boom"
    assert svc.get(s.session_id).summary()["downstream"]["handback_count"] == 1
    assert svc.record_downstream(_evt("aifactory", "failed", "nope")) is False


def test_record_downstream_ambiguous_key_or_wrong_session_stores_nothing(tmp_path):
    svc = PlanService(store_dir=tmp_path)
    a, b = _keyed(svc, "7"), _keyed(svc, "7")
    assert svc.record_downstream(_evt("aifactory", "failed", "7")) is False
    other = {"correlation": {"session_id": a.session_id}}
    assert svc.record_downstream(_evt("aifactory", "failed", "8", **other)) is False
    assert svc.record_downstream(_evt("aifactory", "failed", "7", **other)) is True
    assert svc.get(b.session_id).downstream is None


def test_downstream_survives_restart(tmp_path):
    svc = PlanService(store_dir=tmp_path, persist=True)
    s = _keyed(svc, "42")
    assert svc.record_downstream(_evt("tfactory", "failed", "42"))
    again = PlanService(store_dir=tmp_path, persist=True)
    assert again.get(s.session_id).downstream.handback_count == 1


def test_outcome_history_is_scoped_to_repo_and_tenant(tmp_path):
    svc = PlanService(store_dir=tmp_path)
    old = _keyed(svc, "1")
    other_tenant = _keyed(svc, "2", tenant="t2")
    other_repo = _keyed(svc, "3", repo="o/else")
    for k in ("1", "2", "3"):
        svc.record_downstream(
            _evt("aifactory", "failed", k, halt_reason=f"why-{k}", usage={"cost_usd": 1.0})
        )
    new = _keyed(svc, "9")
    text = svc.outcome_history(new)
    assert "why-1" in text and "why-2" not in text and "why-3" not in text
    assert "token spend $1.00" in text
    assert svc.outcome_history(old) == ""  # only history is its own
    new.repo = None
    assert svc.outcome_history(new) == ""
    assert other_tenant and other_repo


def test_decompose_receives_history_in_the_llm_prompt(tmp_path):
    svc = PlanService(store_dir=tmp_path)
    _keyed(svc, "1")
    svc.record_downstream(_evt("aifactory", "failed", "1", halt_reason="flaky migration"))
    new = _keyed(svc, "9")
    seen: list[str] = []

    class _Llm:
        def complete(self, prompt, *a, **k):
            seen.append(prompt)
            raise RuntimeError("stop")  # falls back to heuristic; prompt already captured

    svc.process(new.session_id, llm=_Llm())
    assert seen and "flaky migration" in seen[0]


def test_record_downstream_ignores_another_tenants_session(tmp_path):
    svc = PlanService(store_dir=tmp_path)
    s = _keyed(svc, "42", tenant="a")
    assert svc.record_downstream(_evt("aifactory", "failed", "42"), tenant_id="b") is False
    assert svc.record_downstream(_evt("aifactory", "failed", "42"), tenant_id="a") is True
    assert svc.get(s.session_id).downstream is not None


def test_record_downstream_reports_a_store_that_did_not_take_the_write():
    store = FakeSessionStore()
    svc = PlanService(session_store=store)
    svc._save(_keyed(svc, "42"))
    store.upsert_error = RuntimeError("db down")
    with pytest.raises(StaleSessionError):
        svc.record_downstream(_evt("aifactory", "failed", "42"))


def test_record_downstream_incomplete_session_list_stores_nothing(tmp_path, monkeypatch):
    svc = PlanService(store_dir=tmp_path)
    _keyed(svc, "42")
    real = svc._list_sessions
    monkeypatch.setattr(svc, "_list_sessions", lambda: (real()[0], False))
    assert svc.record_downstream(_evt("aifactory", "failed", "42")) is False


def test_outcome_history_keeps_unknown_unknown(tmp_path):
    svc = PlanService(store_dir=tmp_path)
    _keyed(svc, "1")
    svc.record_downstream(_evt("aifactory", "failed", "1"))  # no usage, no verification
    text = svc.outcome_history(_keyed(svc, "9"))
    assert "did not verify: unknown" in text and "$0.00" not in text
