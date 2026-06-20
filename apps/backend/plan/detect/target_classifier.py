"""Detect stage: software vs non-software classification (issue #5).

The classifier decides whether a plan describes *software* — which unlocks the
deep path (task breakdown + testing + CI/CD + code-specific review gates) — or a
*non-software* deliverable, which gets review + decomposition only. The result
sets :attr:`NormalizedPlan.target_kind`, and the plan-type descriptors (issue #7)
read it to gate downstream stages.

This is a transparent, dependency-light keyword model (not an LLM): it scores the
plan's combined text against weighted software and non-software signal lexicons.
Transparency matters — a reviewer can see *why* a plan was classified, and the
``undetermined`` band defers to a human rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from plan.models import NormalizedPlan, TargetKind

# Weighted signal lexicons. Strong, unambiguous engineering terms weigh 2;
# softer/ambiguous ones weigh 1.
_SOFTWARE_SIGNALS: dict[str, int] = {
    # strong
    "api": 2,
    "endpoint": 2,
    "microservice": 2,
    "database": 2,
    "schema": 2,
    "migration": 2,
    "kubernetes": 2,
    "docker": 2,
    "ci/cd": 2,
    "cicd": 2,
    "frontend": 2,
    "backend": 2,
    "rest": 2,
    "graphql": 2,
    "webhook": 2,
    "terraform": 2,
    "helm": 2,
    "openshift": 2,
    "sdk": 2,
    "repository": 2,
    "deploy": 2,
    "deployment": 2,
    "authentication": 2,
    "oauth": 2,
    "jwt": 2,
    "unit test": 2,
    "integration test": 2,
    "pull request": 2,
    # softer
    "service": 1,
    "server": 1,
    "function": 1,
    "module": 1,
    "library": 1,
    "cache": 1,
    "queue": 1,
    "latency": 1,
    "throughput": 1,
    "build": 1,
    "pipeline": 1,
    "code": 1,
    "feature": 1,
    "bug": 1,
    "refactor": 1,
    "react": 1,
    "python": 1,
    "typescript": 1,
    "golang": 1,
    "rust": 1,
    "config": 1,
    "environment": 1,
    "release": 1,
    "commit": 1,
    "branch": 1,
}
_NON_SOFTWARE_SIGNALS: dict[str, int] = {
    # strong
    "marketing campaign": 2,
    "hiring": 2,
    "recruit": 2,
    "recruiting": 2,
    "whitepaper": 2,
    "press release": 2,
    "legal": 2,
    "contract": 2,
    "procurement": 2,
    "budget": 2,
    "curriculum": 2,
    "syllabus": 2,
    "event planning": 2,
    "fundraising": 2,
    "brand": 2,
    # softer
    "policy": 1,
    "document": 1,
    "research": 1,
    "training": 1,
    "onboarding": 1,
    "blog": 1,
    "newsletter": 1,
    "survey": 1,
    "interview": 1,
    "workshop": 1,
    "presentation": 1,
    "report": 1,
    "proposal": 1,
    "stakeholder": 1,
}

# Score thresholds for the decision bands.
_MIN_SCORE = 2  # below this on both sides → undetermined
_DOMINANCE = 1.5  # winning side must beat the other by this ratio


@dataclass(frozen=True)
class ClassificationResult:
    """Outcome of classifying a plan's text."""

    kind: TargetKind
    confidence: float  # 0.0–1.0
    software_score: int
    non_software_score: int
    matched: tuple[str, ...] = field(default_factory=tuple)


def _score(text: str, lexicon: dict[str, int]) -> tuple[int, list[str]]:
    low = text.lower()
    total = 0
    hits: list[str] = []
    for term, weight in lexicon.items():
        # word-boundary match for single tokens; substring for phrases/symbols
        if " " in term or "/" in term:
            present = term in low
        else:
            present = re.search(rf"\b{re.escape(term)}\b", low) is not None
        if present:
            total += weight
            hits.append(term)
    return total, hits


def classify_text(text: str) -> ClassificationResult:
    """Classify free text as software / non-software / undetermined."""
    sw, sw_hits = _score(text, _SOFTWARE_SIGNALS)
    nw, nw_hits = _score(text, _NON_SOFTWARE_SIGNALS)

    if sw < _MIN_SCORE and nw < _MIN_SCORE:
        return ClassificationResult("undetermined", 0.0, sw, nw, tuple(sw_hits + nw_hits))

    if sw >= nw * _DOMINANCE and sw >= _MIN_SCORE:
        kind: TargetKind = "software"
        conf = sw / (sw + nw) if (sw + nw) else 0.0
        return ClassificationResult(kind, round(conf, 3), sw, nw, tuple(sw_hits))
    if nw >= sw * _DOMINANCE and nw >= _MIN_SCORE:
        conf = nw / (sw + nw) if (sw + nw) else 0.0
        return ClassificationResult("non-software", round(conf, 3), sw, nw, tuple(nw_hits))

    # Mixed / too close to call.
    return ClassificationResult("undetermined", 0.0, sw, nw, tuple(sw_hits + nw_hits))


def classify_plan(plan: NormalizedPlan) -> ClassificationResult:
    """Classify a plan from its title, description, criteria, and raw text."""
    parts = [plan.title, plan.description, *(c.text for c in plan.criteria)]
    if plan.raw_text:
        parts.append(plan.raw_text)
    return classify_text("\n".join(p for p in parts if p))


def apply(plan: NormalizedPlan) -> NormalizedPlan:
    """Return a copy of ``plan`` with ``target_kind`` set from classification.

    Re-hashed afterwards so ``content_hash`` reflects the (now-set) target kind.
    """
    result = classify_plan(plan)
    return plan.model_copy(update={"target_kind": result.kind}).with_hash()
