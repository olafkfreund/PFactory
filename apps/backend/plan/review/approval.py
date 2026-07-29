"""Human-approval gate with content-hash invalidation (issue #17).

This is the final gate before emission. A human signs off on a
:class:`~plan.review.models.PlanReview` only after the automated gates have
passed; the sign-off is bound to the plan's content hash. If the plan is
edited afterwards, the stored ``plan_hash`` no longer matches the plan's
current content and the approval is invalidated — forcing a fresh review
before the plan can be emitted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from plan.models import NormalizedPlan
from plan.review.models import PlanReview


class ApprovalError(RuntimeError):
    """Raised when an approval action violates the gate's preconditions."""


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _plan_hash(plan: NormalizedPlan) -> str:
    """Return the plan's content hash, computing it if not yet stored."""
    return plan.content_hash or plan.compute_hash()


def _gate_refusal(review: PlanReview) -> str:
    """Say which lens blocks approval, by how much, and what to fix (issue #387).

    Everything here is already in the review being refused; the old message
    ("failed the automated review gates") named none of it, so the only way to
    find the cause was to read the API object and then the source. This message
    is what reaches the user in the 409 detail, so it has to stand alone.
    """
    blockers = review.gate_blockers()
    if not blockers:
        return (
            "cannot approve: the automated review gates have not passed and "
            "there are no lens scores to explain why. Re-run the review gates."
        )
    lines: list[str] = []
    for lens in blockers:
        if lens.score < review.threshold:
            lines.append(
                f"lens {lens.lens!r} scored {lens.score:.2f}, "
                f"below the {review.threshold:.2f} threshold."
            )
        else:
            lines.append(f"lens {lens.lens!r} carries a blocking finding.")
        for finding in lens.findings:
            lines.append(f"  - {finding.title} ({finding.severity})")
            if finding.detail:
                lines.append(f"    {finding.detail}")
    lines.append(
        f"Every lens must clear the threshold; the {review.aggregate_score:.2f} "
        "aggregate is not the test."
    )
    return "cannot approve: " + "\n".join(lines)


def approve(
    review: PlanReview,
    plan: NormalizedPlan,
    *,
    approver: str,
    at: str | None = None,
    feedback: str | None = None,
) -> PlanReview:
    """Record a human approval bound to the plan's content hash.

    The automated gates must have passed first — you cannot approve a plan
    that failed its lens gates.

    Args:
        review: The aggregated review to sign off on.
        plan: The plan being approved (its content hash is captured).
        approver: Identity of the human approving.
        at: Approval timestamp (ISO-8601); defaults to current UTC.
        feedback: Optional note appended to the approval feedback log.

    Returns:
        The same ``review``, mutated with a valid :class:`HumanApproval`.

    Raises:
        ApprovalError: If ``review.gates_passed`` is False.
    """
    if not review.gates_passed:
        raise ApprovalError(_gate_refusal(review))
    if review.readiness is not None:
        unwaived = review.readiness.unwaived_hard_failures(plan)
        if unwaived:
            failing = ", ".join(r.check_id for r in unwaived)
            raise ApprovalError(
                "cannot approve: unwaived hard readiness failures "
                f"({failing}). Fix the plan or record a waiver first."
            )
    approval = review.human_approval
    approval.approved = True
    approval.approved_by = approver
    approval.approved_at = at or _utcnow_iso()
    approval.plan_hash = _plan_hash(plan)
    approval.valid = True
    approval.review_count += 1
    if feedback:
        approval.feedback.append(feedback)
    return review


def reject(
    review: PlanReview,
    plan: NormalizedPlan,
    *,
    approver: str,
    feedback: str,
    at: str | None = None,
) -> PlanReview:
    """Record a human rejection of the plan.

    Args:
        review: The review being rejected.
        plan: The plan under review (unused for state but kept symmetric).
        approver: Identity of the human rejecting.
        feedback: Reason for rejection (required).
        at: Rejection timestamp (ISO-8601); defaults to current UTC.

    Returns:
        The same ``review``, mutated with an invalid :class:`HumanApproval`.
    """
    approval = review.human_approval
    approval.approved = False
    approval.valid = False
    approval.approved_by = approver
    approval.approved_at = at or _utcnow_iso()
    approval.review_count += 1
    if feedback:
        approval.feedback.append(feedback)
    return review


def is_valid(review: PlanReview, plan: NormalizedPlan) -> bool:
    """Return True if the approval is live for the plan's current content.

    The approval must be approved, marked valid, and its recorded
    ``plan_hash`` must still match the plan's current content hash. If the
    plan has been edited since approval, the hashes diverge and this returns
    False (hash invalidation).
    """
    approval = review.human_approval
    return approval.approved and approval.valid and approval.plan_hash == plan.compute_hash()


def revalidate(review: PlanReview, plan: NormalizedPlan) -> PlanReview:
    """Re-evaluate the approval's validity against the plan's current content.

    Sets ``human_approval.valid`` to True only when the approval is still
    approved and its recorded hash matches the plan's current hash. Call this
    after any plan edit.
    """
    approval = review.human_approval
    approval.valid = approval.approved and approval.plan_hash == plan.compute_hash()
    return review


def add_feedback(
    review: PlanReview,
    *,
    author: str,
    text: str,
    at: str | None = None,
) -> PlanReview:
    """Append a timestamped feedback line to the approval log.

    The line is formatted as ``"[ts] author: text"``.
    """
    ts = at or _utcnow_iso()
    review.human_approval.feedback.append(f"[{ts}] {author}: {text}")
    return review


def save_approval(review: PlanReview, path: str | Path) -> Path:
    """Persist ``review`` as JSON and return the written path."""
    target = Path(path)
    target.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    return target


def load_approval(path: str | Path) -> PlanReview:
    """Load a :class:`PlanReview` previously written by :func:`save_approval`."""
    return PlanReview.model_validate_json(Path(path).read_text(encoding="utf-8"))
