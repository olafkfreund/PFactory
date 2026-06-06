"""Map the governance review to the contract's execution.review_tier (epic #65, child 6).

PFactory has already run its review lenses + rules (``plan/review/gates.py``); the
contract should tell AIFactory how strictly to gate the *built* work based on that
outcome:

  - blocking  → a blocking finding stands, or the automated gates did not pass:
                AIFactory must hold for human review before merge.
  - auto      → gates passed with a strong aggregate score and no blockers:
                low-risk, safe to auto-advance.
  - async     → gates passed but the score is middling: review happens, but
                asynchronously (doesn't block the pipeline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.review.models import PlanReview

# Aggregate score at/above which a clean review is safe to auto-advance.
_AUTO_SCORE = 0.9


def derive_review_tier(review: PlanReview) -> str:
    """Return 'blocking' | 'auto' | 'async' for ``review``."""
    if not review.gates_passed or review.blocking_findings():
        return "blocking"
    if review.aggregate_score >= _AUTO_SCORE:
        return "auto"
    return "async"


def attach_review_tier(contract: dict[str, Any], review: PlanReview) -> dict[str, Any]:
    """Attach (in place) ``execution.review_tier`` from the governance review."""
    execution = contract.setdefault("execution", {})
    execution["review_tier"] = derive_review_tier(review)
    return contract
