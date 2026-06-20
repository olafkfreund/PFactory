"""Shared cloud-relevance heuristic for the planning pipeline.

Both the Enrich stage (``service._enrich`` — decide whether to probe live infra
adapters) and the readiness ``enrichment-integrity`` check need the same answer to
"does this plan actually target cloud/cluster infrastructure?". Keeping that
judgment in one place means the check can never disagree with the stage that
produced (or skipped) the enrichment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.models import NormalizedPlan

# Keywords that signal the plan touches cloud / cluster infrastructure.
CLOUD_KEYWORDS: tuple[str, ...] = (
    "aws",
    "eks",
    "ecs",
    "lambda",
    "s3",
    "rds",
    "kubernetes",
    "k8s",
    "openshift",
    "azure",
    "aks",
    "gcp",
    "gke",
    "cloud",
    "helm",
    "terraform",
    "redis",
    "deploy",
    "ingress",
    "microservice",
)

# Plan types that are inherently infra-relevant regardless of wording.
_CLOUD_PLAN_TYPES = ("infra-change", "data-pipeline")


def plan_text(plan: NormalizedPlan) -> str:
    """Concatenate the plan's free text + criteria for keyword scanning."""
    return " ".join(
        [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
    )


def is_cloud_relevant(plan: NormalizedPlan) -> bool:
    """True when the plan targets cloud/cluster infra (keywords or plan_type)."""
    low = plan_text(plan).lower()
    return any(k in low for k in CLOUD_KEYWORDS) or (plan.plan_type in _CLOUD_PLAN_TYPES)
