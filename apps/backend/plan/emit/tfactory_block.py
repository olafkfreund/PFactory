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

from plan.recon.delta import build_ac_to_code_map

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

_DEFAULT_COVERAGE = 0.8
_DEFAULT_API_BASE = "http://localhost:8000"


def _plan_text(plan: NormalizedPlan) -> str:
    return " ".join(
        [
            plan.title,
            plan.description,
            *(c.text for c in plan.criteria),
            plan.raw_text or "",
        ]
    ).lower()


def _any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def _resolved_language(plan: NormalizedPlan) -> str | None:
    """The plan's language: recon's grounded primary, else the spec signal."""
    repo_map = getattr(plan, "repo_map", None)
    if repo_map is not None and getattr(repo_map, "available", False) and repo_map.languages:
        return str(repo_map.languages[0])
    from plan.recon.language_reconcile import detect_spec_language  # noqa: PLC0415

    return detect_spec_language(plan)


def _native_descriptor(plan: NormalizedPlan) -> Any:  # LanguageDescriptor | None
    """The language descriptor for a descriptor-declared plan language.

    None for python/typescript (the hand-tuned path below stays authoritative)
    and for any language without a vendored ``plan/languages/*.yaml``
    descriptor. Swift and Kotlin are the first two; a new language reaches this
    path by descriptor drop alone (RFC-0005 paved road).
    """
    from plan.language_descriptors import load_languages  # noqa: PLC0415

    lang = _resolved_language(plan)
    if not lang:
        return None
    return load_languages().get(lang.lower())


def _native_block(
    plan: NormalizedPlan, epic: EpicPlan, descriptor: Any, wanted: dict[str, bool]
) -> dict[str, Any]:
    """The tfactory block for a descriptor-declared language (swift, kotlin, ...).

    Lanes come from the intersection of what the plan implies and what the
    descriptor has PROVEN runnable; everything the descriptor refuses lands in
    ``unavailable_lanes`` WITH its mandatory reason, so the omission is
    machine-readable (RFC-0006 VAL-0) instead of silent. Before this path
    existed, a Swift plan fell through to the pytest/jest binary and the whole
    environment was silently labelled TypeScript.
    """
    lanes: list[str] = []
    frameworks: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    for lane_key, implied in wanted.items():
        if not implied:
            continue
        lane = descriptor.lane(lane_key)
        if lane is not None and lane.available:
            lanes.append(lane_key)
            frameworks[lane_key] = lane.tool
        else:
            reason = descriptor.unavailable_reason(lane_key)
            if reason:
                unavailable[lane_key] = reason
    block: dict[str, Any] = {
        "language": descriptor.name,
        "lanes": lanes,
        "frameworks": frameworks,
        "coverage_target": _DEFAULT_COVERAGE,
        "mutation_scope": [],
        "security_scope": [],
        "ac_to_code_map": build_ac_to_code_map(plan, epic),
    }
    if unavailable:
        block["unavailable_lanes"] = unavailable
    if "api" in lanes:
        block["endpoints"] = {"api_base_url": _DEFAULT_API_BASE}
    return block


def build_tfactory(plan: NormalizedPlan, epic: EpicPlan) -> dict[str, Any]:
    """Build the ``tfactory`` block from the plan's stack + the epic's kinds."""
    text = _plan_text(plan)
    kinds = {c.kind for c in epic.children}
    labels = {label for c in epic.children for label in c.labels}

    python_ish = _any(text, ("pytest", "python", "fastapi", "django", "flask")) or bool(
        {"testing", "cicd"} & kinds
    )
    node_ish = _any(text, ("jest", "npm", "node", "react", "vitest", "next"))
    # RFC-0010: when reconnaissance read the repo, its actual language wins over
    # the plan-text guess (fixes the #585 wrong-language trap at the source).
    repo_map = getattr(plan, "repo_map", None)
    if repo_map is not None and getattr(repo_map, "available", False) and repo_map.languages:
        primary = repo_map.languages[0]
        python_ish = primary == "python"
        node_ish = primary in ("typescript", "javascript")
    api = _any(text, ("api", "endpoint", "rest", "fastapi", "express", "graphql")) or (
        "area:api" in labels or "service:api" in labels
    )
    browser = _any(text, ("frontend", "react", "playwright", "browser", "vue", "svelte", "next"))
    integration = _any(text, ("integration", "docker", "compose", "database", "postgres", "redis"))

    descriptor = _native_descriptor(plan)
    if descriptor is not None:
        wanted = {"unit": True, "api": api, "browser": browser, "integration": integration}
        return _native_block(plan, epic, descriptor, wanted)

    lanes = ["unit"]
    if api:
        lanes.append("api")
    if browser:
        lanes.append("browser")
    if integration:
        lanes.append("integration")

    unit_fw = "pytest" if (python_ish or not node_ish) else "jest"
    frameworks = {"unit": unit_fw}
    if api:
        frameworks["api"] = "pytest" if python_ish else "jest"
    if browser:
        frameworks["browser"] = "playwright"

    block = {
        "language": "python" if unit_fw == "pytest" else "typescript",
        "lanes": lanes,
        "frameworks": frameworks,
        "coverage_target": _DEFAULT_COVERAGE,
        "mutation_scope": [],
        "security_scope": [],  # app SAST/DAST delegated — out of scope here
        # AC → code map: keyed by every acceptance criterion so TFactory knows the
        # full set that must be covered. RFC-0010 pre-seeds the file lists from
        # reconnaissance when available; empty (filled once code exists) otherwise.
        "ac_to_code_map": build_ac_to_code_map(plan, epic),
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
