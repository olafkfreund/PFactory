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

ChildKind = Literal[
    "feature", "task", "testing", "cicd", "docs", "infra", "research", "chore"
]
Complexity = Literal["simple", "standard", "complex"]


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


class EpicPlan(BaseModel):
    """An epic + its child issues, derived from a NormalizedPlan."""

    plan_id: str
    epic_title: str
    epic_body: str = ""
    children: list[ChildIssue] = Field(default_factory=list)
    summary: str = ""

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
                    in_cycle.update(stack[stack.index(dep):] + [dep])
                elif dep not in state:
                    visit(dep, stack + [dep])
            state[node] = 1

        for node in graph:
            if node not in state:
                visit(node, [node])
        return sorted(in_cycle)
