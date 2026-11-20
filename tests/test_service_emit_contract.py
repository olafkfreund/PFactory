"""PlanService.emit_contract — wiring the Task Contract v2 into the live flow (#65)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.review.gates import run_gates  # noqa: E402
from plan.review.models import LensScore, PlanReview  # noqa: E402
from plan.service import PlanService, PlanServiceError, UserApprovalRequiredError  # noqa: E402
from tests.conftest import persist  # noqa: E402
from tests.fake_session_store import FakeSessionStore  # noqa: E402

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- exposes a widget API
"""


def _processed_session(svc: PlanService) -> str:
    """Ingest + attach a governed epic/review without the yaml-dependent process()."""
    session = svc.ingest_text(_PLAN, title="Widget service")
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[
            ChildIssue(key="C1", title="Scaffold", kind="infra"),
            ChildIssue(
                key="C2",
                title="API",
                kind="feature",
                depends_on=["C1"],
                acceptance_criteria=["exposes a widget API"],
            ),
        ],
    )
    session.review = PlanReview(
        plan_id=session.plan.plan_id,
        lenses=[LensScore(lens="architecture", score=0.95)],
        aggregate_score=0.95,
        gates_passed=True,
    )
    persist(svc, session)
    return session.session_id


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, *, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        return {"taskId": "t-9", "status": "accepted"}


def test_emit_contract_requires_epic() -> None:
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Widget")
    with pytest.raises(PlanServiceError, match="process the plan"):
        svc.emit_contract(session.session_id)


def test_emit_contract_dry_run() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    out = svc.emit_contract(sid, repo="acme/widget", dry_run=True)
    assert out.contract_result["ok"] and out.contract_result["dry_run"]
    contract = out.contract_result["contract"]
    assert contract["contract_version"] == "2"
    assert contract["execution"]["review_tier"] == "auto"
    assert "unit" in contract["tfactory"]["lanes"]
    assert out.status != "emitted"  # dry-run doesn't advance status
    assert out.contract_emit_record is None


def test_emit_contract_live_sets_correlation() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    http = FakeHttp()
    out = svc.emit_contract(
        sid, repo="acme/widget", project_id="p1", http=http, key="secret", dry_run=False
    )
    assert out.contract_result["ok"] and not out.contract_result["dry_run"]
    assert out.status == "emitted"
    assert out.aifactory_task_id == "t-9"
    assert http.calls[0]["url"].endswith("/api/tasks/from-plan")
    # signed
    assert http.calls[0]["json"]["plan"]["approval"]["signature"]


def test_provider_review_approval_emits_signed_stage_handoff() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    session = svc.get(sid)
    handoff = {
        "project_id": "project-1",
        "provider": "github",
        "repo": "acme/widget",
        "base_ref": "dev",
        "source_branch": "factory/1096-widget",
        "approved_head_sha": "a" * 40,
        "pr_number": 77,
    }
    session.stage_state = handoff
    persist(svc, session)
    svc.approve(
        sid,
        approver="alice",
        user_id="7",
        auth_kind="provider_review",
        provider="github",
        provider_user_id=7,
        username="alice",
        pr_number=77,
        approved_head_sha="a" * 40,
    )
    http = FakeHttp()

    class StageProvider:
        def __init__(self):
            self._gh_client = self

        async def fetch_pr(self, _number):
            return SimpleNamespace(state="open", target_branch="dev")

        async def get_pr_head_sha(self, _number):
            return "a" * 40

    out = svc._emit_contract(
        sid,
        repo="acme/widget",
        project_id="project-1",
        http=http,
        key="secret",
        dry_run=False,
        require_user_approval=True,
        stage_provider=StageProvider(),
        stage_handoff=True,
    )
    assert out.status == "emitted"
    assert out.review.human_approval.provider_user_id == 7
    assert {key: http.calls[0]["json"]["plan"]["provenance"][key] for key in handoff} == handoff
    assert set(http.calls[0]["json"]) == {"plan"}


def test_staged_emit_requires_provider_approval_signature_and_current_head(monkeypatch) -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    session = svc.get(sid)
    session.stage_state = {
        "project_id": "p1",
        "provider": "github",
        "repo": "acme/widget",
        "base_ref": "dev",
        "source_branch": "factory/1",
        "pr_number": 77,
        "approved_head_sha": "a" * 40,
    }
    persist(svc, session)
    with pytest.raises(PlanServiceError, match="PR approval"):
        svc.approve(sid, approver="alice", auth_kind="user", user_id="7")
    http = FakeHttp()
    with pytest.raises(PlanServiceError, match="stage machine"):
        svc.emit_contract(sid, repo="acme/widget", http=http, dry_run=False)
    svc.approve(
        sid,
        approver="alice",
        auth_kind="provider_review",
        user_id="7",
        provider="github",
        provider_user_id=7,
        username="alice",
        pr_number=77,
        approved_head_sha="a" * 40,
    )

    class StageProvider:
        def __init__(self, head):
            self.head = head
            self._gh_client = self

        async def fetch_pr(self, _number):
            return SimpleNamespace(state="open", target_branch="dev")

        async def get_pr_head_sha(self, _number):
            return self.head

    with pytest.raises(UserApprovalRequiredError, match="head changed"):
        svc._emit_contract(
            sid,
            repo="acme/widget",
            http=http,
            key="test-key",
            dry_run=False,
            stage_provider=StageProvider("b" * 40),
            stage_handoff=True,
        )
    monkeypatch.setattr("plan.emit.contract_emit.key_from_env", lambda _authority: (None, None))
    with pytest.raises(ValueError, match="signing key"):
        svc._emit_contract(
            sid,
            repo="acme/widget",
            http=http,
            dry_run=False,
            stage_provider=StageProvider("a" * 40),
            stage_handoff=True,
        )
    assert not http.calls


def test_repeated_live_emit_is_idempotent_until_plan_changes() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    http = FakeHttp()
    first = svc.emit_contract(sid, repo="acme/widget", http=http, key="secret", dry_run=False)
    assert first.contract_emit_record["digest"]
    second = svc.emit_contract(sid, repo="acme/widget", http=http, key="secret", dry_run=False)
    assert second.contract_result["duplicate"] is True
    assert len(http.calls) == 1
    session = svc.get(sid)
    session.plan.criteria[0].text = "Changed acceptance criterion"
    persist(svc, session)
    third = svc.emit_contract(sid, repo="acme/widget", http=http, key="secret", dry_run=False)
    assert third.contract_result["ok"]
    assert len(http.calls) == 2
    session = svc.get(sid)
    session.plan.criteria[0].text = "exposes a widget API"
    persist(svc, session)
    again = svc.emit_contract(sid, repo="acme/widget", http=http, key="secret", dry_run=False)
    assert again.contract_result["duplicate"] is True
    assert len(http.calls) == 2
    assert svc.get(sid).contract_result.get("duplicate") is None
    svc.emit_contract(
        sid, repo="acme/widget", project_id="different", http=http, key="secret", dry_run=False
    )
    assert len(http.calls) == 3


def test_live_emit_signs_approved_snapshot_when_cached_plan_changes(monkeypatch) -> None:
    import plan.emit.contract_emit as contract_emit

    svc = PlanService(session_store=FakeSessionStore())
    sid = _processed_session(svc)
    svc.approve(sid, approver="human", user_id="user-1", auth_kind="user")
    original = contract_emit.emit_contract

    def edit_before_signing(*args, **kwargs):
        svc.update_plan(sid, title="Unapproved title")
        return original(*args, **kwargs)

    monkeypatch.setattr(contract_emit, "emit_contract", edit_before_signing)
    http = FakeHttp()
    out = svc.emit_contract(sid, http=http, key="secret", dry_run=False, require_user_approval=True)
    assert out.contract_result["ok"] is True
    assert http.calls[0]["json"]["plan"]["feature"] == "Widget service"
    assert svc.get(sid).plan.title == "Unapproved title"


def test_live_emit_with_readiness_saves_once_under_cas() -> None:
    store = FakeSessionStore()
    svc = PlanService(session_store=store)
    sid = _processed_session(svc)
    session = svc.get(sid)
    session.review = run_gates(session.plan, session.epic)
    persist(svc, session)
    before = store.get(sid)[1]

    out = svc.emit_contract(sid, http=FakeHttp(), key="secret", dry_run=False)

    assert out.contract_result["ok"] is True
    assert store.get(sid)[1] == before + 1
    assert svc.get(sid).contract_emit_records


def test_live_emit_dedupes_trailing_slash_in_destination() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    http = FakeHttp()
    svc.emit_contract(sid, http=http, key="secret", dry_run=False, base_url="http://x:3101")
    duplicate = svc.emit_contract(
        sid, http=http, key="secret", dry_run=False, base_url="http://x:3101/"
    )
    assert duplicate.contract_result["duplicate"] is True
    assert len(http.calls) == 1


def test_duplicate_response_uses_cached_task_id() -> None:
    class TaskHttp(FakeHttp):
        def post(self, url, *, params, json):
            super().post(url, params=params, json=json)
            return {"taskId": f"t-{len(self.calls)}", "status": "accepted"}

    svc = PlanService()
    sid = _processed_session(svc)
    http = TaskHttp()
    svc.emit_contract(sid, http=http, key="secret", dry_run=False)
    session = svc.get(sid)
    original_text = session.plan.criteria[0].text
    session.plan.criteria[0].text = "Changed acceptance criterion"
    persist(svc, session)
    svc.emit_contract(sid, http=http, key="secret", dry_run=False)
    session = svc.get(sid)
    assert session.aifactory_task_id == "t-2"
    session.plan.criteria[0].text = original_text
    persist(svc, session)
    duplicate = svc.emit_contract(sid, http=http, key="secret", dry_run=False)
    assert duplicate.contract_result["duplicate"] is True
    assert duplicate.contract_result["response"]["taskId"] == "t-1"
    assert duplicate.aifactory_task_id == "t-1"
    assert len(http.calls) == 2


def test_live_mcp_emit_requires_valid_user_approval() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    http = FakeHttp()
    with pytest.raises(PlanServiceError, match="authenticated user approval required"):
        svc.emit_contract(sid, http=http, key="secret", dry_run=False, require_user_approval=True)
    assert http.calls == []
    svc.approve(sid, approver="human", user_id="user-1", auth_kind="user")
    result = svc.emit_contract(
        sid, http=http, key="secret", dry_run=False, require_user_approval=True
    )
    assert result.contract_result["ok"] is True
    assert len(http.calls) == 1


def test_emit_contract_live_refused_on_unwaived_hard_readiness() -> None:
    """#326: the gate that blocks approve must block the contract fast path too."""
    svc = PlanService()
    sid = _processed_session(svc)
    session = svc.get(sid)
    # Drop the child covering AC#1 -> ac-child-coverage hard-fails, unwaived.
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[ChildIssue(key="C1", title="Scaffold", kind="infra")],
    )
    session.review = run_gates(session.plan, session.epic)
    session.review.gates_passed = True  # isolate readiness from lens scoring
    assert session.review.readiness.unwaived_hard_failures(session.plan)
    persist(svc, session)

    with pytest.raises(PlanServiceError, match="unwaived hard readiness"):
        svc.emit_contract(
            sid, repo="acme/widget", project_id="p1", http=FakeHttp(), key="secret", dry_run=False
        )
    # dry-run stays open — discovery must still work.
    assert svc.emit_contract(sid, dry_run=True).contract_result["dry_run"]
