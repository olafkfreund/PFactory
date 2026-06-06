"""Per-subtask verification specs + global required_commands (epic #65, child 5).

The contract tells AIFactory/TFactory HOW to prove each subtask is done. We can't
synthesize a real test command from a plan alone, so we stay honest:

  - testing / cicd children      → a ``command`` verification (pytest / lint+test)
  - children with acceptance criteria → a ``manual`` verification carrying the AC
    text as its scenario (a human/agent checks it)
  - docs / criteria-less children → ``none``

``required_commands`` is the global allowlist of verification command *names*
AIFactory grants into its sandbox, detected from the plan's stack signals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.decompose.models import ChildIssue, EpicPlan
    from plan.models import NormalizedPlan


def _plan_text(plan: NormalizedPlan) -> str:
    """Concatenate the plan's free text + criteria for stack-signal scanning."""
    return " ".join(
        [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
    )


def build_verification(child: ChildIssue) -> dict[str, Any]:
    """Derive a subtask's verification spec from its kind + acceptance criteria."""
    if child.kind == "testing":
        return {"type": "command", "run": "pytest"}
    if child.kind == "cicd":
        return {"type": "command", "run": "ruff check . && pytest"}
    if child.kind == "docs":
        return {"type": "none"}
    if child.acceptance_criteria:
        return {"type": "manual", "scenario": "; ".join(child.acceptance_criteria)}
    return {"type": "none"}


def derive_required_commands(plan: NormalizedPlan, epic: EpicPlan) -> list[str]:
    """Detect the verification commands to allowlist, from the plan's stack."""
    text = _plan_text(plan).lower()
    kinds = {c.kind for c in epic.children}
    cmds: set[str] = set()

    python_ish = (
        "pytest" in text or "python" in text or "uv " in text
        or bool({"testing", "cicd"} & kinds)
    )
    if python_ish:
        cmds |= {"uv", "pytest", "ruff"}
    if "mypy" in text:
        cmds.add("mypy")
    if "jest" in text or "npm" in text or "node" in text or "vitest" in text:
        cmds |= {"npm", "jest"}
    if "playwright" in text:
        cmds.add("playwright")
    if "eslint" in text:
        cmds.add("eslint")
    if "tsc" in text or "typescript" in text:
        cmds.add("tsc")
    return sorted(cmds)


def attach_verification(
    contract: dict[str, Any], plan: NormalizedPlan, epic: EpicPlan
) -> dict[str, Any]:
    """Attach (in place) per-subtask verification + global required_commands."""
    for phase in contract.get("phases", []):
        for subtask in phase.get("subtasks", []):
            child = epic.child(subtask.get("id", ""))
            if child is not None:
                subtask["verification"] = build_verification(child)
    cmds = derive_required_commands(plan, epic)
    if cmds:
        contract["required_commands"] = cmds
    return contract
