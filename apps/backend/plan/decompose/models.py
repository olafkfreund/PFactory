"""Decompose-stage data contract: EpicPlan + ChildIssue (issue #6).

The Decompose stage turns a :class:`~plan.models.NormalizedPlan` into an
:class:`EpicPlan` — one epic plus the child issues that implement it. This is the
shape the Emit stage (#16/#17) renders into GitHub issues and AIFactory handoff
payloads, so it mirrors that downstream contract: each child carries a title,
body, labels, a kind, dependencies, and a complexity hint.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChildKind = Literal["feature", "task", "testing", "cicd", "docs", "infra", "research", "chore"]
Complexity = Literal["simple", "standard", "complex"]
Confidence = Literal["low", "medium", "high"]
# How an EpicPlan was produced — recorded so the readiness gate can detect a
# silent fallback from the LLM to the heuristic (gap #6):
#   heuristic     → the deterministic decomposer ran (no LLM requested).
#   llm           → the LLM seam produced a valid EpicPlan.
#   llm_fallback  → the LLM seam was requested but errored; the heuristic ran.
DecomposeMethod = Literal["heuristic", "llm", "llm_fallback"]


class CostLine(BaseModel):
    """One priced resource within a :class:`CostEstimate`."""

    resource: str  # e.g. "ec2:m5.large", "rds:db.r6g.large Multi-AZ"
    quantity: float = 1.0
    monthly_usd: float = 0.0
    detail: str = ""


class CostEstimate(BaseModel):
    """Estimated monthly cloud spend for the proposed work (filled by Phase C).

    ``confidence`` reflects how much of the resource shape we could price from a
    real pricing API vs. infer; ``source`` cites the pricing source so the
    estimate is auditable (issue #7 — always cite).
    """

    monthly_usd: float = 0.0
    currency: str = "USD"
    lines: list[CostLine] = Field(default_factory=list)
    confidence: Confidence = "low"
    source: str = ""  # e.g. "aws-price-list", "azure-retail-prices"


class EffortEstimate(BaseModel):
    """Estimated time/effort, rolled up from child complexity (Phase C)."""

    story_points: int = 0
    duration_days_low: float = 0.0
    duration_days_high: float = 0.0
    confidence: Confidence = "low"
    assumptions: list[str] = Field(default_factory=list)


class AccessRequirement(BaseModel):
    """One capability the plan needs PFactory/the principal to actually have.

    ``granted`` is ``None`` until verified (Phase C IAM policy-simulation /
    region/quota checks): ``True`` = confirmed reachable, ``False`` = denied.
    """

    provider: str = ""  # aws | azure | gcp | kubernetes | ...
    action: str = ""  # e.g. "s3:PutObject", "eks:CreateCluster"
    resource: str = "*"
    region: str = ""
    granted: bool | None = None
    detail: str = ""


class ChildIssue(BaseModel):
    """One child issue under the epic — an AIFactory-executable unit of work."""

    key: str  # stable within an EpicPlan, e.g. "C1" — used for dependencies
    title: str
    body: str = ""
    kind: ChildKind = "feature"
    labels: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)  # other child keys
    complexity: Complexity = "standard"
    acceptance_criteria: list[str] = Field(default_factory=list)
    # Feasibility annotations (filled by Phase C; empty/None until then).
    effort_estimate: EffortEstimate | None = None
    access_requirements: list[AccessRequirement] = Field(default_factory=list)


class EpicPlan(BaseModel):
    """An epic + its child issues, derived from a NormalizedPlan."""

    plan_id: str
    epic_title: str
    epic_body: str = ""
    children: list[ChildIssue] = Field(default_factory=list)
    summary: str = ""
    # Feasibility rollups for the whole epic (filled by Phase C).
    cost_estimate: CostEstimate | None = None
    effort_estimate: EffortEstimate | None = None
    access_requirements: list[AccessRequirement] = Field(default_factory=list)
    # How this epic was produced (gap #6: detect a silent LLM→heuristic fallback).
    decompose_method: DecomposeMethod = "heuristic"
    decompose_errors: list[str] = Field(default_factory=list)

    def child(self, key: str) -> ChildIssue | None:
        return next((c for c in self.children if c.key == key), None)

    def validate_dependencies(self) -> list[str]:
        """Return a list of problems: dangling deps, self-deps, or cycles.

        Empty list means the dependency graph is sane (used by the feasibility
        gate, #14, before emission).
        """
        keys = {c.key for c in self.children}
        problems: list[str] = []
        for c in self.children:
            for dep in c.depends_on:
                if dep == c.key:
                    problems.append(f"{c.key} depends on itself")
                elif dep not in keys:
                    problems.append(f"{c.key} depends on unknown child '{dep}'")
        problems.extend(f"dependency cycle through {k}" for k in self._cycle_keys())
        return problems

    def _cycle_keys(self) -> list[str]:
        graph = {c.key: [d for d in c.depends_on if d != c.key] for c in self.children}
        state: dict[str, int] = {}  # 0=visiting, 1=done
        in_cycle: set[str] = set()

        def visit(node: str, stack: list[str]) -> None:
            state[node] = 0
            for dep in graph.get(node, []):
                if dep not in graph:
                    continue
                if state.get(dep) == 0:  # back-edge → cycle
                    in_cycle.update(stack[stack.index(dep) :] + [dep])
                elif dep not in state:
                    visit(dep, stack + [dep])
            state[node] = 1

        for node in graph:
            if node not in state:
                visit(node, [node])
        return sorted(in_cycle)
