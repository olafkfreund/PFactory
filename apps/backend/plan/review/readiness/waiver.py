"""Recording human waivers of hard readiness failures (epic #33, P0.5).

A waiver is the deliberate, audited override that keeps the hard readiness gate
from being unconditional. It can only clear a check that is *currently a hard
failure* and *waivable*, and it is bound to the plan's content hash — so editing
the plan's substance invalidates the waiver exactly as it invalidates a human
approval.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from plan.models import NormalizedPlan
from plan.review.models import PlanReview
from plan.review.readiness.models import Waiver


class WaiverError(RuntimeError):
    """Raised when a waiver action violates the gate's preconditions."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def waive(
    review: PlanReview,
    plan: NormalizedPlan,
    *,
    check_ids: Sequence[str],
    reason: str,
    waived_by: str,
    at: str | None = None,
) -> PlanReview:
    """Record a waiver covering one or more failing, waivable readiness checks.

    Args:
        review: The review whose readiness report is being waived against.
        plan: The plan under review (its content hash binds the waiver).
        check_ids: Ids of the checks to waive — each must currently be a hard
            failure and be waivable.
        reason: Why the override is acceptable (required, audited).
        waived_by: Identity of the human recording the waiver.
        at: Timestamp (ISO-8601); defaults to current UTC.

    Returns:
        The same ``review``, with a :class:`Waiver` appended to its report.

    Raises:
        WaiverError: If there is no readiness report, no/blank inputs, or any
            named check is unknown, passing, or non-waivable.
    """
    if review.readiness is None:
        raise WaiverError("no readiness report on this review to waive against")
    if not check_ids:
        raise WaiverError("at least one check_id is required to waive")
    if not reason or not reason.strip():
        raise WaiverError("a waiver reason is required")
    if not waived_by or not waived_by.strip():
        raise WaiverError("waived_by (the approver identity) is required")

    report = review.readiness
    for cid in check_ids:
        result = report.result(cid)
        if result is None:
            raise WaiverError(f"unknown readiness check '{cid}'")
        if not result.is_hard_failure():
            raise WaiverError(
                f"check '{cid}' is not a hard failure — there is nothing to waive"
            )
        if not result.waivable:
            raise WaiverError(f"check '{cid}' is not waivable")

    report.waivers.append(
        Waiver(
            check_ids=list(check_ids),
            reason=reason.strip(),
            waived_by=waived_by.strip(),
            waived_at=at or _utcnow_iso(),
            plan_hash=plan.compute_hash(),
            valid=True,
        )
    )
    return review
