"""Tests for the jurisdictions-declared readiness check.

Both directions are asserted — red on a personal-data spec with no
``## Jurisdictions`` section, green once one is added — because a
one-directional test proves nothing about the check's teeth.

Run: apps/backend/.venv/bin/pytest tests/test_readiness_jurisdictions.py
"""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import Criterion, NormalizedPlan
from plan.review.models import PlanReview
from plan.review.readiness.checks import run_readiness
from plan.review.readiness.waiver import waive

PERSONAL_SPEC = "Users sign up, create a personal profile with photos, and chat with people nearby."


def _plan(
    description: str = PERSONAL_SPEC,
    *,
    title: str = "MyFriends app",
    criterion: str = "Users can create a profile",
) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=description,
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text=criterion)],
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(plan_id="001-x", epic_title="x", children=[ChildIssue(key="C1", title="y")])


def _result(plan):
    return run_readiness(plan, _epic()).result("jurisdictions-declared")


def test_red_when_personal_data_and_no_jurisdictions_section() -> None:
    """Direction one: the check must FAIL on the incomplete spec."""
    r = _result(_plan())
    assert r.status == "fail"
    assert r.hard is True
    assert r.waivable is True
    assert r.severity == "high"
    assert "## Jurisdictions" in r.remediation
    assert r.evidence == {"jurisdictions": []}


def test_green_once_a_jurisdictions_section_is_added() -> None:
    """Direction two: the same spec plus a section must PASS."""
    r = _result(_plan(PERSONAL_SPEC + "\n\n## Jurisdictions\nUK, EU, US-California.\n"))
    assert r.status == "pass"
    assert "jurisdictions-section" in r.evidence["jurisdictions"]
    assert "UK" in r.evidence["jurisdictions"]


def test_named_markets_without_a_heading_also_pass() -> None:
    r = _result(_plan(PERSONAL_SPEC + " Target markets: United Kingdom and Germany."))
    assert r.status == "pass"


def test_not_applicable_without_personal_data() -> None:
    r = _result(
        _plan(
            "Rotate the TLS certificates on the ingress controllers.",
            title="Rotate the TLS certificates",
            criterion="New certs are served on every ingress",
        )
    )
    assert r.status == "not_applicable"


def test_hard_failure_blocks_and_a_waiver_clears_it() -> None:
    """The customer can consciously override; the waiver is recorded."""
    plan = _plan()
    review = PlanReview(plan_id=plan.plan_id, gates_passed=True)
    review.readiness = run_readiness(plan, _epic())
    failures = [r.check_id for r in review.readiness.hard_failures()]
    assert "jurisdictions-declared" in failures
    waive(
        review,
        plan,
        check_ids=["jurisdictions-declared"],
        reason="Markets to be declared at contract stage",
        waived_by="customer",
    )
    unwaived = [r.check_id for r in review.readiness.unwaived_hard_failures(plan)]
    assert "jurisdictions-declared" not in unwaived
    assert review.readiness.waivers
    assert review.readiness.waivers[0].covers("jurisdictions-declared")
