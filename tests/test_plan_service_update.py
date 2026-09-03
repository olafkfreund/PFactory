"""Tests for the revise loop — edit a plan, re-process, re-approve (#692).

Before this, PlanEditor's edits never left the browser: /process took only a
session_id, no PATCH route existed, and the client's `updateAndProcess` helper
(written against "a hypothetical PATCH") was dead code. Editing the description
and hitting Re-process silently re-ran the ORIGINAL plan and returned 200, so
the UI read as if the edit had been reviewed.

The approval-invalidation test is the important one: a review attests to the
text that was reviewed, so an edit must not be able to inherit it.
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

from plan.service import PlanInputError, PlanService, PlanServiceError  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT and rejects unauthenticated callers
"""


def _processed(svc: PlanService):
    session = svc.ingest_text(_PLAN, title="Refund API")
    return svc.process(session.session_id)


def test_update_plan_persists_each_authored_field():
    svc = PlanService()
    sid = _processed(svc).session_id

    svc.update_plan(
        sid,
        title="Refund API v2",
        description="Now with a lawful basis stated.",
        criteria=[{"id": "AC#1", "text": "Refunds require an approver"}],
    )

    # Read back through get() rather than the return value. Note this alone
    # cannot prove the edit was PERSISTED -- the in-memory store hands back the
    # same object update_plan mutated, so dropping `_save` still passes here.
    # test_edit_survives_a_restart covers that; verified by mutation.
    stored = svc.get(sid).plan
    assert stored.title == "Refund API v2"
    assert stored.description == "Now with a lawful basis stated."
    assert [(c.id, c.text) for c in stored.criteria] == [
        ("AC#1", "Refunds require an approver")
    ]


def test_omitted_fields_are_left_alone():
    svc = PlanService()
    sid = _processed(svc).session_id
    before = svc.get(sid).plan
    original_title, original_criteria = before.title, list(before.criteria)

    svc.update_plan(sid, description="only this changes")

    after = svc.get(sid).plan
    assert after.description == "only this changes"
    assert after.title == original_title
    assert [c.text for c in after.criteria] == [c.text for c in original_criteria]


def test_editing_invalidates_review_and_approval():
    svc = PlanService()
    sid = _processed(svc).session_id
    svc.approve(sid, approver="olaf")
    assert svc.get(sid).status == "approved"

    svc.update_plan(sid, description="text the approver never saw")

    session = svc.get(sid)
    assert session.status == "ingested", "an edit must not stay approved"
    assert session.review is None, "an edit must not inherit the old review"


def test_empty_title_and_empty_criteria_are_rejected():
    svc = PlanService()
    sid = _processed(svc).session_id

    with pytest.raises(PlanServiceError):
        svc.update_plan(sid, title="   ")
    with pytest.raises(PlanServiceError):
        svc.update_plan(sid, criteria=[])


def test_update_after_process_is_reviewed_against_the_new_text():
    """The whole point of the loop: fix a gap, re-run, get a fresh verdict."""
    svc = PlanService()
    sid = _processed(svc).session_id

    svc.update_plan(sid, criteria=[{"id": "AC#1", "text": "Refunds need an approver"}])
    reprocessed = svc.process(sid)

    assert reprocessed.status == "processed"
    assert reprocessed.review is not None
    assert [c.text for c in reprocessed.plan.criteria] == ["Refunds need an approver"]


def test_edit_survives_a_restart(tmp_path):
    """Persistence, on the durable path — the in-memory store cannot show this.

    With `persist=True` a second PlanService over the same store_dir is a
    restart. Dropping `_save` from update_plan fails here and nowhere else.
    """
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = _processed(svc).session_id
    svc.update_plan(sid, description="survives a restart")

    restarted = PlanService(store_dir=tmp_path, persist=True)
    assert restarted.get(sid).plan.description == "survives a restart"


def test_malformed_criterion_is_a_client_error_not_a_KeyError():
    """A criterion arrives over HTTP; a missing key is input, not a crash.

    Indexing `c["id"]` blind turned a malformed body into a KeyError and a 500
    (PR #696 review). PlanInputError subclasses PlanServiceError, so the older
    tests that catch the base type still hold.
    """
    svc = PlanService()
    sid = _processed(svc).session_id

    for bad in ({"text": "no id"}, {"id": "AC#1"}, {}):
        with pytest.raises(PlanInputError) as caught:
            svc.update_plan(sid, criteria=[bad])
        assert "criterion 0 is missing" in str(caught.value)

    # And the plan is untouched by a rejected edit.
    assert [c.text for c in svc.get(sid).plan.criteria] != ["no id"]


def test_input_errors_are_distinguishable_from_a_missing_session():
    """The route answers 400 vs 404 off these types, so the split must hold."""
    svc = PlanService()
    sid = _processed(svc).session_id

    with pytest.raises(PlanInputError):
        svc.update_plan(sid, title="  ")
    with pytest.raises(PlanServiceError) as unknown:
        svc.update_plan("no-such-session", title="fine")
    assert not isinstance(unknown.value, PlanInputError)
