"""Readiness-stage data contracts: ReadinessCheckResult, Waiver, ReadinessReport.

A :class:`ReadinessReport` is the output of running the readiness checks
(``checks.py``) against a plan + its epic. Each :class:`ReadinessCheckResult`
records one deterministic verdict; a hard ``fail`` blocks emission unless a valid
:class:`Waiver` covers it. Waivers reuse the same content-hash binding the
human-approval gate uses (:meth:`plan.models.NormalizedPlan.compute_hash`) — edit
the plan's substance and the waiver (like the approval) is invalidated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from plan.review.models import Citation, Severity
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from plan.models import NormalizedPlan

# pass            → the requirement is satisfied (incl. "verified absent").
# fail            → the requirement is violated (the gate's teeth).
# not_applicable  → the check does not apply to this plan (e.g. no cloud).
# skipped         → the check could not be evaluated (missing prerequisite).
CheckStatus = Literal["pass", "fail", "not_applicable", "skipped"]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReadinessCheckResult(BaseModel):
    """One readiness check's verdict for a plan."""

    check_id: str  # stable, e.g. "criteria-present"
    title: str
    status: CheckStatus = "pass"
    severity: Severity = "info"
    hard: bool = False  # a hard fail blocks emission (unless waived)
    waivable: bool = True  # may a human waive a hard fail of this check?
    detail: str = ""
    remediation: str = ""  # one-line fix hint
    citations: list[Citation] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)  # machine-readable

    def is_hard_failure(self) -> bool:
        return self.hard and self.status == "fail"


class Waiver(BaseModel):
    """A recorded human override of one or more failing readiness checks.

    Bound to the plan's content hash at waive time; editing the plan's substance
    diverges the hash and (via :meth:`ReadinessReport.revalidate`) invalidates the
    waiver, forcing a fresh decision — exactly as a human approval is invalidated.
    """

    check_ids: list[str]
    reason: str
    waived_by: str
    waived_at: str = Field(default_factory=_utcnow_iso)
    plan_hash: str = ""  # NormalizedPlan.content_hash at waive time
    valid: bool = True

    def covers(self, check_id: str) -> bool:
        return self.valid and check_id in self.check_ids


class ReadinessReport(BaseModel):
    """Aggregated readiness verdict for a plan: results + any waivers."""

    plan_id: str
    plan_hash: str = ""  # hash the report was computed against
    results: list[ReadinessCheckResult] = Field(default_factory=list)
    waivers: list[Waiver] = Field(default_factory=list)
    generated_at: str = Field(default_factory=_utcnow_iso)

    # ── queries ────────────────────────────────────────────────────────

    def result(self, check_id: str) -> ReadinessCheckResult | None:
        return next((r for r in self.results if r.check_id == check_id), None)

    def hard_failures(self) -> list[ReadinessCheckResult]:
        """Every hard check that failed, regardless of waivers."""
        return [r for r in self.results if r.is_hard_failure()]

    def _is_waived(self, check_id: str, plan: NormalizedPlan | None) -> bool:
        """True if a still-valid waiver covers ``check_id``.

        When ``plan`` is supplied the waiver's hash is re-checked against the
        plan's current content (the strict, edit-aware path); otherwise the
        waiver's stored ``valid`` flag is trusted.
        """
        current = plan.compute_hash() if plan is not None else None
        for w in self.waivers:
            if check_id not in w.check_ids:
                continue
            if current is not None:
                if w.plan_hash == current:
                    return True
            elif w.valid:
                return True
        return False

    def unwaived_hard_failures(
        self, plan: NormalizedPlan | None = None
    ) -> list[ReadinessCheckResult]:
        """Hard failures not cleared by a valid waiver."""
        return [
            r for r in self.hard_failures() if not self._is_waived(r.check_id, plan)
        ]

    def is_ready(self, plan: NormalizedPlan | None = None) -> bool:
        """True when no hard failure remains unwaived."""
        return not self.unwaived_hard_failures(plan)

    def revalidate(self, plan: NormalizedPlan) -> ReadinessReport:
        """Re-evaluate every waiver against the plan's current content hash.

        Call after any plan edit (mirrors ``approval.revalidate``).
        """
        current = plan.compute_hash()
        for w in self.waivers:
            w.valid = w.plan_hash == current
        return self
