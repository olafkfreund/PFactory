"""Tests for assembling + emitting the full Task Contract (epic #65, child 8)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_emit import assemble_contract, emit_contract
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan
from plan.review.models import LensScore, PlanReview


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        description="fastapi service tested with pytest",
        criteria=[Criterion(id="AC#1", text="exposes an API")], **kw,
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget", epic_title="Widget",
        children=[
            ChildIssue(key="C1", title="Scaffold", kind="infra"),
            ChildIssue(key="C2", title="API", kind="feature", depends_on=["C1"],
                       acceptance_criteria=["exposes an API"]),
        ],
    )


def _review(passed: bool = True, score: float = 0.95) -> PlanReview:
    return PlanReview(
        plan_id="001-widget",
        lenses=[LensScore(lens="architecture", score=score)],
        aggregate_score=score, gates_passed=passed,
    )


class FakeHttp:
    def __init__(self, fail_urls: tuple[str, ...] = ()) -> None:
        self.calls: list[dict] = []
        self.fail_urls = fail_urls

    def post(self, url, *, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        if any(f in url for f in self.fail_urls):
            raise RuntimeError("endpoint unavailable")
        return {"taskId": "t-123", "status": "accepted"}


def test_assemble_is_complete_and_valid() -> None:
    contract = assemble_contract(_plan(), _epic(), _review())
    assert validate_contract(contract) == []
    assert contract["execution"]["skip_planning"] is True
    assert contract["execution"]["review_tier"] == "auto"
    assert "unit" in contract["tfactory"]["lanes"]
    # every subtask got a verification spec
    subs = [st for ph in contract["phases"] for st in ph["subtasks"]]
    assert all("verification" in st for st in subs)


def test_dry_run_does_not_post() -> None:
    http = FakeHttp()
    result = emit_contract(_plan(), _epic(), _review(), base_url="http://ai:3101",
                           project_id="p1", http=http, dry_run=True)
    assert result["ok"] and result["dry_run"]
    assert result["endpoint"].endswith("/api/tasks/from-plan")
    assert http.calls == []  # nothing posted


def test_sign_when_key_provided() -> None:
    result = emit_contract(_plan(), _epic(), _review(), base_url="http://ai:3101",
                           project_id="p1", key="secret", approval_timestamp="2026-06-06T00:00:00Z",
                           dry_run=True)
    assert result["signed"] is True
    approval = result["contract"]["approval"]
    assert approval["approved_by"] == "pfactory"
    assert len(approval["signature"]) == 64  # sha256 hex


def test_live_posts_to_from_plan() -> None:
    http = FakeHttp()
    result = emit_contract(_plan(), _epic(), _review(), base_url="http://ai:3101",
                           project_id="p1", http=http, key="secret",
                           approval_timestamp="2026-06-06T00:00:00Z", dry_run=False)
    assert result["ok"] and not result["dry_run"] and not result["fallback"]
    assert http.calls[0]["url"].endswith("/api/tasks/from-plan")
    assert http.calls[0]["json"]["approval"]["signature"]


def test_falls_back_to_create_and_run() -> None:
    http = FakeHttp(fail_urls=("/from-plan",))
    result = emit_contract(_plan(), _epic(), _review(), base_url="http://ai:3101",
                           project_id="p1", http=http, dry_run=False)
    assert result["ok"] and result["fallback"]
    # second call was the create-and-run fallback
    assert any("create-and-run" in c["url"] for c in http.calls)


def test_invalid_contract_not_emitted() -> None:
    empty_epic = EpicPlan(plan_id="001-widget", epic_title="Widget", children=[])
    http = FakeHttp()
    result = emit_contract(_plan(), empty_epic, _review(), base_url="http://ai:3101",
                           project_id="p1", http=http, dry_run=False)
    assert not result["ok"]
    assert result["errors"]
    assert http.calls == []  # never posted
