"""Adapter feeding provider best-practice findings into the review gates (#23/#24).

The review rules engine exposes an external-policy seam: :func:`run_gates`
accepts ``external_runner`` — a callable ``(NormalizedPlan, EpicPlan) -> list[Finding]``
(see ``plan/review/gates.py`` and ``plan/review/rules/engine.py``). This module
provides exactly such a callable that builds a read-only ``context`` dict from the
plan and runs every available registered provider via
:func:`plan.providers.base.run_all`.

Usage::

    from plan.providers.review_runner import provider_runner
    from plan.review.gates import run_gates

    review = run_gates(plan, epic, external_runner=provider_runner)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plan.providers.base import run_all
from plan.review.models import Finding

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# Import the built-in provider modules so they self-register via their
# ``@register_provider`` decorators when this adapter is used.
import plan.providers.aws  # noqa: E402,F401
import plan.providers.azure  # noqa: E402,F401
import plan.providers.gcp  # noqa: E402,F401
import plan.providers.terraform  # noqa: E402,F401


def build_context(plan: NormalizedPlan, epic: EpicPlan | None = None) -> dict:
    """Build a read-only assessment context dict from a plan (and optional epic).

    Surfaces the plan's free text and any enrichment infra/IaC entries so each
    provider can locate the targets it understands (cloud account, region,
    Terraform dir, etc.). Defensive against missing/oddly shaped attributes.
    """
    text_parts: list[str] = []
    for attr in ("title", "description", "raw_text"):
        value = getattr(plan, attr, None)
        if value:
            text_parts.append(str(value))
    for crit in getattr(plan, "criteria", []) or []:
        crit_text = getattr(crit, "text", None)
        if crit_text:
            text_parts.append(str(crit_text))

    enrichment = getattr(plan, "enrichment", None)
    infra = list(getattr(enrichment, "infra", []) or []) if enrichment else []
    knowledge = list(getattr(enrichment, "knowledge", []) or []) if enrichment else []

    # IaC entries are infra findings flagged as iac/terraform — surface them too.
    iac = [
        entry
        for entry in infra
        if isinstance(entry, dict)
        and str(entry.get("kind", "")).lower() in {"iac", "terraform", "hcl"}
    ]

    return {
        "plan_id": getattr(plan, "plan_id", ""),
        "text": "\n".join(text_parts),
        "infra": infra,
        "iac": iac,
        "knowledge": knowledge,
    }


def provider_runner(plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
    """External-policy runner: collect findings from all available providers.

    Compatible with ``run_gates(..., external_runner=provider_runner)``. Returns
    real provider findings PLUS advisory suggest-install notes for any provider
    that's relevant to the plan but not installed (#3). Never raises.
    """
    from plan.providers.registry import suggest_installs

    context = build_context(plan, epic)
    findings = run_all(context)
    try:
        findings.extend(suggest_installs(context))
    except Exception:
        pass
    return findings
