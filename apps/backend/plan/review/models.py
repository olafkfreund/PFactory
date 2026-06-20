"""Review-stage data contracts: Finding, LensScore, PlanReview (issues #15–#17).

The review gates evaluate a plan through several *lenses* (architecture,
security, best-practices, feasibility). Each lens returns a :class:`LensScore`
(0–1 plus :class:`Finding` notes); the gate aggregates them against a threshold
into a :class:`PlanReview`. A :class:`HumanApproval` record (bound to the plan's
content hash) gates emission — and is invalidated when the plan changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from plan.models import NormalizedPlan
    from plan.review.readiness.models import ReadinessReport

Severity = Literal["info", "low", "medium", "high", "critical"]
LensName = Literal["architecture", "security", "best-practices", "feasibility"]


class Citation(BaseModel):
    """A documentation source backing a finding's recommended change.

    PFactory *helps, never overrides*: whenever a lens/rule asks for a change it
    must say WHY and point at a source the engineer can read — a knowledge ref, a
    provider/cloud doc, an ADR, or a template policy. ``why`` is the one-line
    justification; ``uri``/``title`` locate the source; ``source`` names where the
    citation came from (e.g. ``"knowledge:backstage"``, ``"provider:terraform"``,
    ``"policy:gcp-project"``, ``"aws-well-architected"``).
    """

    why: str = ""
    uri: str = ""
    title: str = ""
    source: str = ""


class Finding(BaseModel):
    """One observation from a lens or a deterministic policy check."""

    title: str
    detail: str = ""
    severity: Severity = "info"
    source: str = ""  # lens name, or "checkov"/"opa"/"llm"/"cloud-mcp"
    blocking: bool = False
    # Doc-source backing when this finding proposes a change (issue #7). A finding
    # that merely reports (e.g. an info note) may leave this empty; one that asks
    # the engineer to change something SHOULD carry at least one citation.
    citations: list[Citation] = Field(default_factory=list)


class LensScore(BaseModel):
    """The result of evaluating a plan through one review lens."""

    lens: str
    score: float = 0.0  # 0–1
    max: float = 1.0
    findings: list[Finding] = Field(default_factory=list)
    blocking: bool = False  # this lens hard-fails the gate when it has a blocker

    def has_blocking_finding(self) -> bool:
        return any(f.blocking for f in self.findings)


class HumanApproval(BaseModel):
    """Human sign-off, bound to the plan content hash (issue #17)."""

    approved: bool = False
    approved_by: str = ""
    approved_at: str = ""
    plan_hash: str = ""  # NormalizedPlan.content_hash at approval time
    valid: bool = False
    review_count: int = 0
    feedback: list[str] = Field(default_factory=list)


class PlanReview(BaseModel):
    """Aggregated review of a plan across all lenses + human approval."""

    plan_id: str
    lenses: list[LensScore] = Field(default_factory=list)
    threshold: float = 0.75
    aggregate_score: float = 0.0
    gates_passed: bool = False
    code_gates_applied: bool = True
    human_approval: HumanApproval = Field(default_factory=HumanApproval)
    # Hard completeness gate, orthogonal to the scored lenses (epic #33). None
    # until run_gates attaches it; when present, its unwaived hard failures also
    # block emission. See plan/review/readiness/.
    readiness: ReadinessReport | None = None

    def recompute(self) -> PlanReview:
        """Recompute ``aggregate_score`` and ``gates_passed`` from the lenses.

        Gates pass when every lens scores at/above ``threshold`` AND no lens
        carries a blocking finding. The aggregate is the mean lens score.
        """
        if self.lenses:
            self.aggregate_score = round(sum(ls.score for ls in self.lenses) / len(self.lenses), 4)
        else:
            self.aggregate_score = 0.0
        all_pass = bool(self.lenses) and all(ls.score >= self.threshold for ls in self.lenses)
        no_blockers = not any(ls.has_blocking_finding() for ls in self.lenses)
        self.gates_passed = all_pass and no_blockers
        return self

    def blocking_findings(self) -> list[Finding]:
        return [f for ls in self.lenses for f in ls.findings if f.blocking]

    def ready_to_emit(self, plan: NormalizedPlan | None = None) -> bool:
        """True only when gates passed, approval is valid, AND readiness holds.

        ``plan`` is optional for back-compat with existing zero-arg callers; pass
        it for the strict, edit-aware readiness check (a waiver is only honoured
        when its hash still matches the plan's current content). When a
        :class:`ReadinessReport` is attached, any unwaived hard failure blocks
        emission; with no report attached, behaviour is unchanged.
        """
        base = self.gates_passed and self.human_approval.approved and self.human_approval.valid
        if self.readiness is None:
            return base
        return base and self.readiness.is_ready(plan)


# Resolve the forward-referenced ``readiness`` field. Imported at module end (not
# top) to break the cycle: readiness.models imports Citation/Severity from here,
# which are defined above before this runs.
from plan.review.readiness.models import ReadinessReport

PlanReview.model_rebuild()
