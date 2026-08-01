"""Tests for assembling + emitting the full Task Contract (epic #65, child 8)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_emit import assemble_contract, emit_contract
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan
from plan.review.models import LensScore, PlanReview


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget",
        title="Widget",
        source_format="markdown",
        description="fastapi service tested with pytest",
        criteria=[Criterion(id="AC#1", text="exposes an API")],
        **kw,
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget",
        epic_title="Widget",
        children=[
            ChildIssue(key="C1", title="Scaffold", kind="infra"),
            ChildIssue(
                key="C2",
                title="API",
                kind="feature",
                depends_on=["C1"],
                acceptance_criteria=["exposes an API"],
            ),
        ],
    )


def _review(passed: bool = True, score: float = 0.95) -> PlanReview:
    return PlanReview(
        plan_id="001-widget",
        lenses=[LensScore(lens="architecture", score=score)],
        aggregate_score=score,
        gates_passed=passed,
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


def test_assemble_attaches_rfc0014_routing() -> None:
    # RFC-0014: assemble_contract runs the cost router after apply_tier and writes
    # execution.phase_models + execution.routing; the contract still validates.
    contract = assemble_contract(_plan(), _epic(), _review())
    assert validate_contract(contract) == []
    routing = contract["execution"]["routing"]
    assert routing["class"] in {"economy", "standard", "premium", "governed"}
    assert "autonomy" in routing and "verdict" in routing["autonomy"]
    pm = contract["execution"]["phase_models"]
    # router owns at least the planning role.
    assert "planning" in pm


def test_dry_run_does_not_post() -> None:
    http = FakeHttp()
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        http=http,
        dry_run=True,
    )
    assert result["ok"] and result["dry_run"]
    assert result["endpoint"].endswith("/api/tasks/from-plan")
    assert http.calls == []  # nothing posted


def test_sign_when_key_provided() -> None:
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        key="secret",
        approval_timestamp="2026-06-06T00:00:00Z",
        dry_run=True,
    )
    assert result["signed"] is True
    approval = result["contract"]["approval"]
    assert approval["approved_by"] == "pfactory"
    assert len(approval["signature"]) == 64  # sha256 hex


def test_emit_stamps_the_kid_from_the_environment(monkeypatch) -> None:
    """#401: the live signing path must produce a revocable envelope.

    Pre-fix this emitted a four-field envelope with no ``kid`` at all, so
    AIFactory's AIFACTORY_TRUSTED_PLAN_RETIRED_KIDS had nothing to revoke.
    """
    import hashlib
    import hmac

    from plan.emit.signing import _signing_bytes

    monkeypatch.setenv("AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q3", "secret")
    monkeypatch.delenv("PFACTORY_TRUSTED_PLAN_KID", raising=False)
    ts = "2026-06-06T00:00:00Z"
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        approval_timestamp=ts,
        dry_run=True,
    )
    assert result["signed"] is True
    approval = result["contract"]["approval"]
    assert approval["kid"] == "2026q3"
    # And it verifies the way AIFactory verifies it: kid bound into the bytes.
    expected = hmac.new(
        b"secret",
        _signing_bytes(result["contract"], "pfactory", ts, "2", "2026q3"),
        hashlib.sha256,
    ).hexdigest()
    assert approval["signature"] == expected


def test_emit_without_a_keyed_var_still_signs_the_legacy_way(monkeypatch) -> None:
    # Back-compat: today's deployment has only the unkeyed var and must keep
    # producing the exact envelope AIFactory already accepts.
    monkeypatch.setenv("AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY", "secret")
    monkeypatch.delenv("PFACTORY_TRUSTED_PLAN_KID", raising=False)
    monkeypatch.delenv("AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q3", raising=False)
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        approval_timestamp="2026-06-06T00:00:00Z",
        dry_run=True,
    )
    assert result["signed"] is True
    assert "kid" not in result["contract"]["approval"]


def test_live_posts_to_from_plan() -> None:
    http = FakeHttp()
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        http=http,
        key="secret",
        approval_timestamp="2026-06-06T00:00:00Z",
        dry_run=False,
    )
    assert result["ok"] and not result["dry_run"] and not result["fallback"]
    assert http.calls[0]["url"].endswith("/api/tasks/from-plan")
    assert http.calls[0]["json"]["plan"]["approval"]["signature"]


def test_falls_back_to_create_and_run() -> None:
    http = FakeHttp(fail_urls=("/from-plan",))
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        http=http,
        dry_run=False,
    )
    assert result["ok"] and result["fallback"]
    # second call was the create-and-run fallback
    assert any("create-and-run" in c["url"] for c in http.calls)


class _AssertHttp(FakeHttp):
    """Both endpoints raise AssertionError — the real #321 failure shape
    (an AssertionError bubbling out of urllib on the live emit)."""

    def post(self, url, *, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        raise AssertionError("urllib blew up")


def test_both_endpoints_failing_returns_error_not_500() -> None:
    # #321: from-plan fails AND the create-and-run fallback fails. The handoff
    # must surface a clean error dict, never let the exception escape as a 500.
    http = _AssertHttp()
    result = emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        http=http,
        dry_run=False,
    )
    assert result["ok"] is False
    assert result["dry_run"] is False
    assert result["errors"]
    joined = " ".join(result["errors"])
    assert "from-plan failed" in joined and "fallback also failed" in joined
    # both endpoints were attempted
    assert any("from-plan" in c["url"] for c in http.calls)
    assert any("create-and-run" in c["url"] for c in http.calls)


def test_invalid_contract_not_emitted() -> None:
    empty_epic = EpicPlan(plan_id="001-widget", epic_title="Widget", children=[])
    http = FakeHttp()
    result = emit_contract(
        _plan(),
        empty_epic,
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        http=http,
        dry_run=False,
    )
    assert not result["ok"]
    assert result["errors"]
    assert http.calls == []  # never posted
