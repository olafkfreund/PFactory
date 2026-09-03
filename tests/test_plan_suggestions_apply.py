"""Accepting a suggestion must actually change the plan (#701).

Before this, `SuggestedEdit.suggestion` was literally the review finding's title
(`annotate.py`: `suggestion=f.title`) and there was no replacement text anywhere
in the model — so the Suggestions tab could be read and never acted on.

The tests that matter here are the ones that would pass against a FAKE apply:
appending a "TODO: address X" stub would satisfy "the description changed", so
these assert the drafted text lands, that a tag draft is in the shape the tag
check actually scans for, and that an accepted suggestion never silently
vanishes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.annotate.models import SuggestedEdit  # noqa: E402
from plan.annotate.remediate import draft_replacement, with_replacements  # noqa: E402
from plan.review.models import Citation  # noqa: E402
from plan.service import PlanInputError, PlanService  # noqa: E402
from plan.templates.loader import build_context  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT and rejects unauthenticated callers
"""


@pytest.fixture
def service():
    return PlanService(persist=False)


@pytest.fixture
def processed(service):
    session = service.ingest_text(_PLAN, title="Refund API")
    return service.process(session.session_id)


def _seed(session, *suggestions: SuggestedEdit):
    """Attach suggestions to a session as the annotate stage would."""
    from plan.annotate.models import AnnotationResult

    session.annotation = AnnotationResult(suggestions=with_replacements(list(suggestions)))
    return session


# ── the drafts ────────────────────────────────────────────────────────────


def test_a_missing_tag_drafts_the_shape_the_tag_check_scans_for():
    """Not an approximation of the fix — the fix.

    `templates/loader.py` collects tags by scanning for `key:` / `key=`, so this
    asserts through that scanner rather than eyeballing the string.
    """
    replacement, mode = draft_replacement(SuggestedEdit(suggestion="missing required tag 'owner'"))

    assert mode == "append_tag"
    from plan.models import NormalizedPlan

    plan = NormalizedPlan(plan_id="p", title="t", description=replacement, source_format="markdown")
    assert "owner" in build_context(plan)["tags"]


def test_a_gdpr_finding_drafts_a_section_matched_on_its_citation():
    replacement, mode = draft_replacement(
        SuggestedEdit(
            suggestion="Something reworded by the lens",
            citation=Citation(title="GDPR Art. 6 - Lawfulness of processing", uri="http://x"),
        )
    )

    assert mode == "insert_section"
    assert replacement.startswith("## Data protection")


def test_a_business_decision_is_left_as_a_placeholder_not_invented():
    """An invented minimum age would READ as decided and pass the gate that
    exists to notice it is not."""
    replacement, _ = draft_replacement(
        SuggestedEdit(suggestion="Lawful basis and purpose limitation not stated")
    )

    assert "<" in replacement and ">" in replacement


def test_a_finding_with_no_curated_remedy_gets_no_draft():
    """No draft beats a fake one — a stub would fix nothing while looking fixed."""
    replacement, mode = draft_replacement(SuggestedEdit(suggestion="Oversized epic"))

    assert (replacement, mode) == ("", "manual")


# ── applying ──────────────────────────────────────────────────────────────


def test_accepting_a_tag_suggestion_puts_the_tag_in_the_plan(service, processed):
    _seed(processed, SuggestedEdit(id="S1", suggestion="missing required tag 'owner'"))

    session, applied = service.apply_suggestions(processed.session_id, [{"id": "S1"}])

    assert [a["id"] for a in applied] == ["S1"]
    assert "owner" in build_context(session.plan)["tags"]


def test_the_human_edited_text_is_what_lands(service, processed):
    """The whole flow is 'propose, human approves' — their edit must win."""
    _seed(processed, SuggestedEdit(id="S1", suggestion="missing required tag 'owner'"))

    session, _ = service.apply_suggestions(
        processed.session_id, [{"id": "S1", "replacement": "owner: platform-team"}]
    )

    assert "owner: platform-team" in session.plan.description


def test_accepting_a_criterion_rewrite_replaces_that_criterion(service, processed):
    target = processed.plan.criteria[0].id
    _seed(
        processed,
        SuggestedEdit(id="S1", suggestion=f"Ambiguous, untestable criterion ({target})"),
    )

    session, _ = service.apply_suggestions(
        processed.session_id, [{"id": "S1", "replacement": "Refunds settle within 5 seconds"}]
    )

    rewritten = next(c for c in session.plan.criteria if c.id == target)
    assert rewritten.text == "Refunds settle within 5 seconds"
    assert len(session.plan.criteria) == len(processed.plan.criteria), "no criterion lost"


def test_applying_invalidates_the_review_like_any_edit(service, processed):
    _seed(processed, SuggestedEdit(id="S1", suggestion="missing required tag 'owner'"))
    service.approve(processed.session_id, approver="olaf")

    session, _ = service.apply_suggestions(processed.session_id, [{"id": "S1"}])

    assert session.status == "ingested"
    assert session.review is None


# ── refusing, rather than silently skipping ───────────────────────────────


def test_an_unknown_id_is_refused(service, processed):
    _seed(processed, SuggestedEdit(id="S1", suggestion="missing required tag 'owner'"))

    with pytest.raises(PlanInputError):
        service.apply_suggestions(processed.session_id, [{"id": "S9"}])


def test_a_suggestion_with_no_draft_is_refused_not_skipped(service, processed):
    """An accepted suggestion that quietly did not land is the worst outcome."""
    _seed(processed, SuggestedEdit(id="S1", suggestion="Oversized epic"))

    with pytest.raises(PlanInputError) as caught:
        service.apply_suggestions(processed.session_id, [{"id": "S1"}])

    assert "no replacement text" in str(caught.value)


def test_a_stale_criterion_target_is_refused(service, processed):
    _seed(processed, SuggestedEdit(id="S1", suggestion="Ambiguous, untestable criterion (AC#99)"))

    with pytest.raises(PlanInputError) as caught:
        service.apply_suggestions(processed.session_id, [{"id": "S1", "replacement": "x"}])

    assert "no longer in this plan" in str(caught.value)
