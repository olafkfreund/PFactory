"""Compute the contract's ``tfactory`` verify profile (epic #65, child 7).

The VERIFY profile PFactory hands down so TFactory doesn't have to infer the test
shape: which lanes to run, the framework per lane, where the API lives, a coverage
target, and the acceptance-criteria → code map. Detection is from the plan's stack
signals + the epic's kinds. Application SAST/DAST is out of scope by default
(``security_scope`` empty), per the product's stance that app-security scanning is
delegated to dedicated pipelines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

_DEFAULT_COVERAGE = 0.8
_DEFAULT_API_BASE = "http://localhost:8000"


def _plan_text(plan: NormalizedPlan) -> str:
    return " ".join(
        [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
    ).lower()


def _any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def build_tfactory(plan: NormalizedPlan, epic: EpicPlan) -> dict[str, Any]:
    """Build the ``tfactory`` block from the plan's stack + the epic's kinds."""
    text = _plan_text(plan)
    kinds = {c.kind for c in epic.children}
    labels = {label for c in epic.children for label in c.labels}

    python_ish = _any(text, ("pytest", "python", "fastapi", "django", "flask")) or bool(
        {"testing", "cicd"} & kinds
    )
    node_ish = _any(text, ("jest", "npm", "node", "react", "vitest", "next"))
    api = _any(text, ("api", "endpoint", "rest", "fastapi", "express", "graphql")) or (
        "area:api" in labels or "service:api" in labels
    )
    browser = _any(text, ("frontend", "react", "playwright", "browser", "vue", "svelte", "next"))
    integration = _any(text, ("integration", "docker", "compose", "database", "postgres", "redis"))

    lanes = ["unit"]
    if api:
        lanes.append("api")
    if browser:
        lanes.append("browser")
    if integration:
        lanes.append("integration")

    unit_fw = "pytest" if (python_ish or not node_ish) else "jest"
    frameworks: dict[str, str] = {"unit": unit_fw}
    if api:
        frameworks["api"] = "pytest" if python_ish else "jest"
    if browser:
        frameworks["browser"] = "playwright"

    block: dict[str, Any] = {
        "lanes": lanes,
        "frameworks": frameworks,
        "coverage_target": _DEFAULT_COVERAGE,
        "mutation_scope": [],
        "security_scope": [],  # app SAST/DAST delegated — out of scope here
        # AC → code map: keyed by every acceptance criterion so TFactory knows the
        # full set that must be covered; the file/function lists are filled once
        # code exists (empty at plan time).
        "ac_to_code_map": {c.id: [] for c in plan.criteria},
    }
    if api:
        block["endpoints"] = {"api_base_url": _DEFAULT_API_BASE}
    return block


def attach_tfactory(
    contract: dict[str, Any], plan: NormalizedPlan, epic: EpicPlan
) -> dict[str, Any]:
    """Attach (in place) the ``tfactory`` block to a built contract; return it."""
    contract["tfactory"] = build_tfactory(plan, epic)
    return contract
