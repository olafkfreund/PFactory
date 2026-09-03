"""Attach the compliance obligations block to the Task Contract.

The compliance lens (``plan/review/lenses/compliance.py``) raises cited
findings during review — but a flag that stays inside PFactory is not a
control. This module carries those obligations onto the signed contract so they
travel to AIFactory as build constraints and to TFactory as things that must be
evidenced.

The block: ``{available, source, disclaimer, jurisdictions[], data_classes[],
obligations[{title, detail, severity, blocking, citations[]}]}``. Like
:mod:`plan.emit.constitution`, attachment is **best-effort and never raises**,
and an ``available: false`` block is still recorded when no compliance lens ran
— so "the lens never ran" is distinguishable from "the lens ran and found
nothing" (which is ``available: true`` with empty ``obligations``).

IMPORTANT — the block is a *descriptive obligations signpost*, not legal
advice; it carries the lens's disclaimer verbatim and makes no compliance
determination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.models import NormalizedPlan
    from plan.review.models import PlanReview

_LENS_NAME = "compliance"
_SOURCE = "pfactory:compliance-lens"


def build_compliance_block(plan: NormalizedPlan, review: PlanReview | None) -> dict[str, Any]:
    """Build the contract ``compliance`` block from the plan + its review.

    ``available`` is True only when the review actually contains a compliance
    lens score; obligations are that lens's findings, citations included.
    """
    from plan.review.lenses.compliance import (  # noqa: PLC0415 - lazy: keep the review stage out of the emit import graph
        DISCLAIMER,
        declared_jurisdictions,
        detect_data_classes,
        scan_text,
    )

    lens_score = None
    if review is not None:
        lens_score = next((ls for ls in review.lenses if ls.lens == _LENS_NAME), None)
    if lens_score is None:
        return {
            "available": False,
            "source": _SOURCE,
            "disclaimer": DISCLAIMER,
            "jurisdictions": [],
            "data_classes": [],
            "obligations": [],
        }

    obligations = [
        {
            "title": f.title,
            "detail": f.detail,
            "severity": f.severity,
            "blocking": f.blocking,
            "citations": [
                {"why": c.why, "uri": c.uri, "title": c.title, "source": c.source}
                for c in f.citations
            ],
        }
        for f in lens_score.findings
    ]
    return {
        "available": True,
        "source": _SOURCE,
        "disclaimer": DISCLAIMER,
        "jurisdictions": declared_jurisdictions(plan),
        "data_classes": detect_data_classes(scan_text(plan)),
        "obligations": obligations,
    }


def attach_compliance(
    contract: dict[str, Any], plan: NormalizedPlan, review: PlanReview | None
) -> dict[str, Any]:
    """Attach the ``compliance`` block to the contract in place.

    Mirrors :func:`plan.emit.constitution.attach_constitution`: additive (the
    schema's top-level ``additionalProperties`` is ``true``), best-effort, and
    **never raises** — a compliance lookup must never break emit. Returns the
    contract for composability.
    """
    try:
        contract["compliance"] = build_compliance_block(plan, review)
        return contract
    except Exception:  # noqa: BLE001 — best-effort: the compliance block must never break emit
        return contract
