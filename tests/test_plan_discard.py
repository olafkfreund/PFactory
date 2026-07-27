"""Abandoning a plan session (#360).

The bug: three ``parr-regression-probe`` sessions ingested on 2026-07-23 sat in
the CFactory cockpit's Active list for days and NOTHING could clear them.

* ``reject`` refused — it writes into ``session.review``, so it requires a
  processed session, and these were only ever ingested.
* Even after processing, ``rejected`` maps to ``human_review`` ("needs attention
  / edit"), so the card stays active. Only ``approved``/``emitted`` reached
  ``done``, and using either would have recorded junk probes as approved plans.

So the honest exit did not exist. These tests pin the one that does.
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

from plan.completion import TERMINAL_STATUSES  # noqa: E402
from plan.service import PlanService  # noqa: E402
from plan.service_helpers import board_state  # noqa: E402

# Deliberately the shape of the probe that exposed this: a title, one AC, and
# nothing else. It is the minimum a session can be and still be un-clearable.
_PROBE = """# probe

## Acceptance Criteria
- AC#1: GET /healthz returns 200
"""


def _ingested() -> tuple[PlanService, str]:
    svc = PlanService()
    return svc, svc.ingest_text(_PROBE, title="parr-regression probe").session_id


# ── the case that had no exit ────────────────────────────────────────────────


def test_an_ingested_session_can_be_discarded_without_ever_being_processed():
    """MUTATION GUARD: the reported bug, asserted at the state it got stuck in.

    ``reject`` cannot reach here — it needs a review. If ``discard`` ever grows
    the same precondition, this fails.
    """
    svc, sid = _ingested()
    assert svc.get(sid).status == "ingested"
    assert svc.get(sid).review is None  # the precondition reject demands

    session = svc.discard(sid, actor="olafkfreund", reason="abandoned probe")

    assert session.status == "discarded"


def test_a_discarded_session_leaves_the_active_board():
    """MUTATION GUARD: map ``discarded`` to anything non-terminal and this fails.

    This is the assertion that matters. A status that says "discarded" while the
    card still sits in the cockpit fixes nothing — the whole defect was a session
    that could not leave the board.
    """
    svc, sid = _ingested()
    assert svc.get(sid).board_state() == "backlog"  # visible as queued

    svc.discard(sid, actor="olafkfreund", reason="abandoned probe")

    assert svc.get(sid).board_state() == "done"
    assert board_state("discarded", None) == "done"


def test_discarded_is_terminal_so_the_completion_event_fires():
    """Without this, CFactory holds the work item open on a dead session."""
    assert "discarded" in TERMINAL_STATUSES


# ── the record ───────────────────────────────────────────────────────────────


def test_the_reason_is_recorded_because_nothing_else_survives_a_discard():
    """A discard leaves no plan, no review and no issue — this dict IS the record."""
    svc, sid = _ingested()

    svc.discard(sid, actor="olafkfreund", reason="mis-ingested regression probe")

    rec = svc.get(sid).discard
    assert rec["actor"] == "olafkfreund"
    assert rec["reason"] == "mis-ingested regression probe"
    assert rec["at"]


def test_the_record_keeps_the_status_it_was_abandoned_from():
    """Distinguishes a probe binned unseen from a reviewed plan someone dropped.

    Regression guard: ``from_status`` is read BEFORE the status is overwritten.
    Capture it afterwards and every discard reports the same value, which is the
    easy way to write this and silently useless.
    """
    svc, sid = _ingested()
    svc.discard(sid, actor="olafkfreund", reason="junk")
    assert svc.get(sid).discard["from_status"] == "ingested"

    svc2 = PlanService()
    sid2 = svc2.ingest_text(_PROBE, title="probe 2").session_id
    svc2.process(sid2)
    svc2.discard(sid2, actor="olafkfreund", reason="changed my mind")
    assert svc2.get(sid2).discard["from_status"] == "processed"


# ── it stays distinct from reject ────────────────────────────────────────────


def test_reject_still_refuses_an_unprocessed_session():
    """Discard must not have loosened reject's precondition as a side effect.

    The two mean different things: reject says *fix this plan*, and a plan that
    was never processed has nothing to fix.
    """
    svc, sid = _ingested()
    with pytest.raises(Exception, match="process the plan before rejecting"):
        svc.reject(sid, approver="olafkfreund", feedback="no")


def test_reject_still_lands_in_human_review_not_done():
    """Rejection is an edit request, and must keep its place on the board."""
    assert board_state("rejected", None) == "human_review"


# ── idempotence ──────────────────────────────────────────────────────────────


def test_discarding_twice_is_a_no_op_rather_than_an_error():
    """A cleanup action that 400s on a retry invites the double-click that causes it."""
    svc, sid = _ingested()
    first = svc.discard(sid, actor="olafkfreund", reason="junk")
    again = svc.discard(sid, actor="someone-else", reason="different reason")

    assert again.status == "discarded"
    # The ORIGINAL record survives — a retry must not rewrite who abandoned it.
    assert again.discard["actor"] == "olafkfreund"
    assert again.discard["reason"] == "junk"
    assert again.discard["at"] == first.discard["at"]
