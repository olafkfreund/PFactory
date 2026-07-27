"""Tests for the planning completion-event envelope + correlation chain (#47).

Conforms to the Factory correlation-key RFC (olafkfreund/Factory#4):
``{correlation_key, service, task_id, status, phase, updated_at}`` on terminal
status, with the upstream issue# / downstream aifactory-task chain persisted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan import completion  # noqa: E402
from plan import service as service_mod  # noqa: E402
from plan.agent_api import plan_status  # noqa: E402
from plan.completion import (  # noqa: E402
    SERVICE_NAME,
    TERMINAL_STATUSES,
    build_completion_event,
    correlation_key_for,
    notify_completion,
)
from plan.emit import github_emitter  # noqa: E402
from plan.emit.github_emitter import EmitResult  # noqa: E402
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund flow
Add a refund flow to the orders web app.
## Acceptance Criteria
- A finance user can issue a refund
- The order status becomes refunded
"""

_RFC_ENVELOPE_KEYS = {
    "correlation_key",
    "service",
    "task_id",
    "status",
    "phase",
    "updated_at",
}


def _processed_session(svc: PlanService):
    session = svc.ingest_text(_PLAN, title="Refund flow")
    return svc.process(session.session_id)


# ── envelope shape (the RFC contract) ────────────────────────────────────────


def test_event_carries_the_rfc_envelope_fields():
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    event = build_completion_event(session, now="2026-06-04T00:00:00+00:00")

    assert _RFC_ENVELOPE_KEYS <= set(event)  # all six RFC fields present
    assert event["service"] == SERVICE_NAME == "pfactory"
    assert event["task_id"] == session.session_id
    assert event["updated_at"] == "2026-06-04T00:00:00+00:00"
    # The chain sub-object carries the upstream/downstream links.
    assert event["correlation"]["session_id"] == session.session_id


def test_terminal_statuses_are_emitted_rejected_and_discarded():
    """``discarded`` joined the set in #360 — a session abandoned outright.

    Kept as an exact-equality assertion rather than loosened to a subset check:
    a status silently becoming terminal is how CFactory would stop tracking work
    that is still running, so a new entry should have to be added here on purpose.
    """
    assert TERMINAL_STATUSES == frozenset({"emitted", "rejected", "discarded"})


# ── correlation key: issue# with synthetic fallback ──────────────────────────


def test_correlation_key_is_synthetic_before_any_issue():
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    assert correlation_key_for(session) == f"pf-{session.session_id}"


def test_correlation_key_becomes_issue_number_after_emit(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    monkeypatch.setattr(
        github_emitter,
        "emit_to_github",
        lambda *a, **k: EmitResult(dry_run=False, epic_number=101, errors=[]),
    )
    out = svc.emit(session.session_id, repo="acme/widget", dry_run=False)

    assert out.status == "emitted"
    assert out.emitted_issue_number == 101
    assert out.correlation_key == "101"
    assert build_completion_event(out)["correlation_key"] == "101"
    assert build_completion_event(out)["correlation"]["issue_number"] == 101


# ── terminal transitions fire exactly the right notification ─────────────────


def test_emit_fires_completion_on_live_success(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    monkeypatch.setattr(
        github_emitter,
        "emit_to_github",
        lambda *a, **k: EmitResult(dry_run=False, epic_number=7, errors=[]),
    )
    fired: list = []
    monkeypatch.setattr(service_mod, "notify_completion", lambda s, **k: fired.append(s) or {})

    svc.emit(session.session_id, repo="acme/widget", dry_run=False)
    assert len(fired) == 1 and fired[0].status == "emitted"


def test_dry_run_emit_does_not_fire_completion(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    fired: list = []
    monkeypatch.setattr(service_mod, "notify_completion", lambda s, **k: fired.append(s) or {})

    out = svc.emit(session.session_id, repo="acme/widget", dry_run=True)
    assert out.status != "emitted"  # dry-run is not terminal
    assert fired == []  # so nothing is notified


def test_reject_is_terminal_and_fires_synthetic_event(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    fired: list = []
    monkeypatch.setattr(service_mod, "notify_completion", lambda s, **k: fired.append(s) or {})

    out = svc.reject(session.session_id, approver="olaf", feedback="no")
    assert out.status == "rejected"
    assert out.correlation_key == f"pf-{out.session_id}"  # synthetic — no issue#
    assert len(fired) == 1


def test_process_does_not_set_a_correlation_key():
    svc = PlanService()
    out = _processed_session(svc)
    assert out.status == "processed"  # not terminal
    assert out.correlation_key is None  # so no key yet


# ── transport is best-effort (webhook + sentinel) ────────────────────────────


def test_webhook_posts_the_envelope_when_configured(monkeypatch):
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    monkeypatch.setenv("PFACTORY_COMPLETION_WEBHOOK", "http://localhost:9/hook")

    captured: dict = {}

    class _Resp:
        def close(self):
            pass

    def _fake_urlopen(req, timeout=0):
        import json

        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    event = notify_completion(session)
    assert captured["url"] == "http://localhost:9/hook"
    assert captured["body"] == event
    assert captured["body"]["service"] == "pfactory"


def test_webhook_failure_never_raises(monkeypatch):
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    monkeypatch.setenv("PFACTORY_COMPLETION_WEBHOOK", "http://localhost:9/hook")

    def _boom(req, timeout=0):
        raise OSError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    event = notify_completion(session)  # must not raise
    assert event["service"] == "pfactory"


def test_no_webhook_configured_is_a_noop(monkeypatch):
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    monkeypatch.delenv("PFACTORY_COMPLETION_WEBHOOK", raising=False)
    # Should simply build + return the event without attempting any POST.
    assert notify_completion(session)["task_id"] == session.session_id


def test_sentinel_written_when_enabled(monkeypatch, tmp_path):
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL", "1")
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL_DIR", str(tmp_path))
    monkeypatch.delenv("PFACTORY_COMPLETION_WEBHOOK", raising=False)

    event = notify_completion(session)
    sentinel = tmp_path / session.session_id / "COMPLETED.json"
    assert sentinel.exists()
    import json

    assert json.loads(sentinel.read_text())["correlation_key"] == event["correlation_key"]


def test_sentinel_skipped_without_dir(monkeypatch, tmp_path):
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL", "1")
    monkeypatch.delenv("PFACTORY_COMPLETION_SENTINEL_DIR", raising=False)
    monkeypatch.delenv("PFACTORY_COMPLETION_WEBHOOK", raising=False)
    notify_completion(session)  # no dir → silent skip, no raise


# ── the chain is observable through the agent/MCP API ────────────────────────


def test_plan_status_surfaces_the_correlation_chain(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    # Point the agent_api singleton at our service for this test.
    import plan.agent_api as agent_api

    monkeypatch.setattr(agent_api, "SERVICE", svc)
    monkeypatch.setattr(
        github_emitter,
        "emit_to_github",
        lambda *a, **k: EmitResult(dry_run=False, epic_number=55, errors=[]),
    )
    svc.emit(session.session_id, repo="acme/widget", dry_run=False)

    status = plan_status(session.session_id)
    assert status["correlation_key"] == "55"
    assert status["issue_number"] == 55
    assert "aifactory_task_id" in status


# ── RFC-0001a evidence gate: emit with no issues is not a pass ────────────────


def test_emit_with_no_epic_is_downgraded_to_failed(monkeypatch):
    # An "emitted" success that created no epic issue produced no governed work
    # item — the evidence gate downgrades it to failed.
    svc = PlanService()
    session = _processed_session(svc)
    monkeypatch.setattr(
        github_emitter,
        "emit_to_github",
        lambda *a, **k: EmitResult(dry_run=False, epic_number=None, errors=[]),
    )
    out = svc.emit(session.session_id, repo="acme/widget", dry_run=False)
    event = build_completion_event(out)
    assert event["status"] == "failed"
    assert "no_evidence" in (event.get("halt_reason") or "")
    assert event["evidence"]["proof_kind"] == "issues"


def test_real_emit_passes_and_carries_issue_evidence(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    monkeypatch.setattr(
        github_emitter,
        "emit_to_github",
        lambda *a, **k: EmitResult(dry_run=False, epic_number=101, errors=[]),
    )
    out = svc.emit(session.session_id, repo="acme/widget", dry_run=False)
    event = build_completion_event(out)
    assert event["status"] == "emitted"
    assert event["evidence"]["epic_issue"] == 101


def test_contract_handoff_with_no_epic_stays_emitted(monkeypatch):
    # Trusted-plan / contract path: emit-contract signs the contract straight to
    # AIFactory and starts the build, creating NO GitHub issues. The started
    # build (aifactory_task_id) is valid evidence, so the gate must not downgrade
    # it to failed — that was surfacing as false plan-stage failures in the
    # cockpit for issue-less runs.
    svc = PlanService()
    session = _processed_session(svc)
    monkeypatch.setattr(
        github_emitter,
        "emit_to_github",
        lambda *a, **k: EmitResult(dry_run=False, epic_number=None, errors=[]),
    )
    out = svc.emit(session.session_id, repo="acme/widget", dry_run=False)
    out.aifactory_task_id = "9d2ce435:031-python-hello-world-greeting-li"
    event = build_completion_event(out)
    assert event["status"] == "emitted"
    assert "halt_reason" not in event
    assert event["evidence"]["proof_kind"] == "contract"
    assert event["evidence"]["aifactory_task_id"] == "9d2ce435:031-python-hello-world-greeting-li"


# ── running cost: usage snapshot for a non-terminal (processed) session ───────


def test_emit_usage_snapshot_reports_running_cost_when_not_terminal(monkeypatch):
    from plan.usage import PlanUsage

    svc = PlanService()
    session = _processed_session(svc)  # any internal emit is a no-op at zero usage
    posted: list = []
    monkeypatch.setattr(completion, "_post_webhook", lambda e: posted.append(e))
    session.usage = PlanUsage(input_tokens=1000, output_tokens=500, model="claude-sonnet-4-6")

    ev = completion.emit_usage_snapshot(session)
    assert ev is not None
    assert len(posted) == 1
    # Non-terminal status + the accrued usage block.
    assert posted[0]["status"] == "processed"
    assert posted[0]["status"] not in TERMINAL_STATUSES
    assert posted[0]["usage"]["total_tokens"] == 1500


def test_emit_usage_snapshot_noop_without_usage(monkeypatch):
    svc = PlanService()
    session = _processed_session(svc)
    posted: list = []
    monkeypatch.setattr(completion, "_post_webhook", lambda e: posted.append(e))
    session.usage = None  # nothing spent

    assert completion.emit_usage_snapshot(session) is None
    assert posted == []
