"""End-to-end: readiness wired through run_gates → waive → approve → emit (P0.6)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.github_emitter import emit_to_github
from plan.models import Criterion, NormalizedPlan
from plan.review.approval import ApprovalError, approve
from plan.review.gates import run_gates
from plan.review.readiness.waiver import waive


class FakeGh:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self._next = 100

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        self._next += 1
        self.created.append({"title": title, "labels": labels})
        return self._next

    def link_sub_issue(self, parent: int, child: int) -> None:  # noqa: D401
        pass


def _plan() -> NormalizedPlan:
    # non-software so code lenses are skipped — keeps the lens score deterministic
    # and lets us isolate the readiness gate as the emit blocker.
    return NormalizedPlan(
        plan_id="001-widget",
        title="Publish the quarterly report",
        description="Assemble and publish the Q report.",
        source_format="markdown",
        target_kind="non-software",
        criteria=[
            Criterion(id="AC#1", text="report is assembled"),
            Criterion(id="AC#2", text="report is published to the portal"),
        ],
    ).with_hash()


def _epic_uncovered() -> EpicPlan:
    # AC#2 has no child → ac-child-coverage hard-fails (waivable).
    return EpicPlan(
        plan_id="001-widget",
        epic_title="Quarterly report",
        children=[
            ChildIssue(key="C1", title="Assemble report", acceptance_criteria=["report is assembled"]),
            ChildIssue(key="C2", title="Design the cover", depends_on=["C1"]),
        ],
    )


def test_run_gates_attaches_readiness_and_flags_uncovered_ac() -> None:
    plan, epic = _plan(), _epic_uncovered()
    review = run_gates(plan, epic)
    assert review.readiness is not None
    cov = review.readiness.result("ac-child-coverage")
    assert cov.status == "fail"
    assert cov.evidence["uncovered_acs"] == ["AC#2"]
    # Even if the scored lenses pass, readiness blocks emit-readiness.
    assert review.ready_to_emit(plan) is False


def test_full_flow_blocks_then_waive_approve_emit() -> None:
    plan, epic = _plan(), _epic_uncovered()
    review = run_gates(plan, epic)

    # Approval is refused while a hard readiness failure stands unwaived.
    if review.gates_passed:
        try:
            approve(review, plan, approver="olaf")
            raised = False
        except ApprovalError:
            raised = True
        assert raised, "approve should refuse an unwaived hard readiness failure"

    # Record the audited waiver, then approve.
    waive(review, plan, check_ids=["ac-child-coverage"], reason="AC#2 tracked in #999", waived_by="olaf")
    review.gates_passed = True  # isolate readiness from lens scoring for this e2e
    approve(review, plan, approver="olaf")
    assert review.ready_to_emit(plan) is True

    # Live emit now proceeds with a fake runner.
    gh = FakeGh()
    result = emit_to_github(epic, repo="acme/widget", review=review, plan=plan, gh=gh, dry_run=False)
    assert not result.errors
    assert result.epic_number is not None
    assert len(gh.created) == 3  # epic + 2 children


def test_emit_refused_when_readiness_unwaived_even_if_approved() -> None:
    """Emit must honour readiness independently of a (stale) approval."""
    plan, epic = _plan(), _epic_uncovered()
    review = run_gates(plan, epic)
    review.gates_passed = True
    # Forge a valid-looking approval to prove emit still checks readiness.
    review.human_approval.approved = True
    review.human_approval.valid = True
    review.human_approval.plan_hash = plan.compute_hash()
    assert review.ready_to_emit(plan) is False

    result = emit_to_github(epic, repo="acme/widget", review=review, plan=plan, gh=FakeGh(), dry_run=False)
    assert result.errors and "readiness" in result.errors[0]
