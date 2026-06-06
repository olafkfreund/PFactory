"""Readiness/completeness gate for the planning pipeline (epic #33).

The review gates (``plan/review/``) score a plan for *quality*; readiness asks a
different, harder question: **is all the information needed to execute this plan
actually present and verified?** Where the lenses are soft and scored, readiness
is a set of deterministic pass/fail checks, several of which *hard-block*
emission until they pass or a human records an explicit, audited waiver.

This mirrors the rules engine (``plan/review/rules/``) in shape — a ``@check``
registry of small pure functions — but its results live on a dedicated
:class:`ReadinessReport` attached to the :class:`~plan.review.models.PlanReview`,
kept orthogonal to the 0.75 lens threshold so a high average can never mask a
missing-information blocker.
"""

from __future__ import annotations

from plan.review.readiness.checks import (
    default_checks,
    register_check,
    run_readiness,
)
from plan.review.readiness.models import (
    CheckStatus,
    ReadinessCheckResult,
    ReadinessReport,
    Waiver,
)
from plan.review.readiness.waiver import WaiverError, waive

__all__ = [
    "CheckStatus",
    "ReadinessCheckResult",
    "ReadinessReport",
    "Waiver",
    "WaiverError",
    "default_checks",
    "register_check",
    "run_readiness",
    "waive",
]
