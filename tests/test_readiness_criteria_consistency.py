"""A worked example may not contradict an invariant in the same plan (#402).

Live case, session ``034-invoice-line-total-endpoint``: AC2 requires
``total = net + vat`` "to the penny, with no discrepancy"; AC3 works the example
``net`` 10.00, ``vat`` 1.75, ``total`` 11.76. 10.00 + 1.75 is 11.75, so AC3 is
unsatisfiable while AC2 holds. Every lens scored 1.0, no readiness check failed,
``/approve`` succeeded and ``/emit-contract`` signed it — a 4-worker build wave
was spent before anything noticed. Every number needed to see it was in the text.

The criteria below are the verbatim strings the live service returns for those
sessions (``GET /api/plan/sessions/<id>``), so this file *is* the reproduction.

The control that must never move is the false-positive side. Sessions 027, 029
and 030 are the same money shape, phrased the other way round
(``net + vat equals total``), with two worked examples each — all legitimately
consistent. A gate that flagged those would be waived reflexively within a week
and would then measure nothing, which is the defect it exists to fix.
"""

from __future__ import annotations

import pytest

from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import Criterion, NormalizedPlan
from plan.review.approval import ApprovalError
from plan.review.models import PlanReview
from plan.review.readiness.checks import (
    _numeric_bindings,
    _stated_relations,
    default_checks,
    run_readiness,
)
from plan.review.readiness.revision import _SOURCE_MODULES
from plan.service import PlanService

CHECK_ID = "criteria-self-consistent"

# ── the live 034 criteria, verbatim ───────────────────────────────────────

LIVE_034 = [
    (
        "AC#1",
        'AC1: `POST /api/line-total` with `{"unit_price": 10.00, "quantity": 3, '
        '"vat_rate": 0.2}` returns HTTP 200 and a body where `net` is 30.00, `vat` '
        "is 6.00 and `total` is 36.00.",
    ),
    (
        "AC#2",
        "AC2: The arithmetic is defined as `net` = half-up round of `unit_price * "
        "quantity`, `vat` = half-up round of `net * vat_rate`, and `total` = `net` + "
        "`vat`. For every accepted request the returned `total` MUST equal the "
        "returned `net` plus the returned `vat` to the penny, with no discrepancy.",
    ),
    (
        "AC#3",
        'AC3: `POST /api/line-total` with `{"unit_price": 10.00, "quantity": 1, '
        '"vat_rate": 0.175}` returns HTTP 200 with `net` 10.00, `vat` 1.75 and '
        "`total` 11.76.",
    ),
    (
        "AC#4",
        "AC4: A negative `unit_price`, a `quantity` below 1, or a `vat_rate` outside "
        "the range 0 to 1 inclusive returns HTTP 422 and never a 500.",
    ),
]

# ── the live control plans, verbatim (027 / 029 / 030 share these) ────────

LIVE_VAT_QUOTE = [
    (
        "AC#1",
        'AC1: `POST /api/quote` with `{"subtotal": 100.00, "vat_rate": 0.2}` returns '
        "HTTP 200 and a body where `net` is 100.00, `vat` is 20.00 and `total` is "
        "120.00.",
    ),
    (
        "AC#2",
        "AC2: Every monetary value in the response is rounded to 2 decimal places "
        'using half-up rounding (ties round away from zero). `{"subtotal": 1.005, '
        '"vat_rate": 0}` MUST return `total` 1.01, and `{"subtotal": 2.675, '
        '"vat_rate": 0}` MUST return `total` 2.68.',
    ),
    (
        "AC#3",
        "AC3: `discount_pct` is applied to `subtotal` BEFORE VAT is calculated. "
        '`{"subtotal": 100.00, "vat_rate": 0.2, "discount_pct": 10}` returns '
        "`discount` 10.00, `net` 90.00, `vat` 18.00 and `total` 108.00.",
    ),
    (
        "AC#5",
        "AC5: For every accepted request, `net` + `vat` equals `total` to the penny.",
    ),
]


def _plan(criteria: list[tuple[str, str]]) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="034-invoice-line-total-endpoint",
        title="Invoice line-total endpoint",
        source_format="markdown",
        criteria=[Criterion(id=i, text=t) for i, t in criteria],
    ).with_hash()


def _epic(plan: NormalizedPlan) -> EpicPlan:
    return EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[
            ChildIssue(
                key="C1", title=plan.title, acceptance_criteria=[c.text for c in plan.criteria]
            )
        ],
    )


def _result(criteria: list[tuple[str, str]]):
    plan = _plan(criteria)
    return run_readiness(plan, _epic(plan)).result(CHECK_ID)


# ── the reproduction ──────────────────────────────────────────────────────


def test_live_034_contradiction_is_detected() -> None:
    result = _result(LIVE_034)
    assert result.status == "fail"
    # The reviewer must be able to confirm the arithmetic without re-reading the
    # plan, so the detail carries both criteria and the sum.
    assert "AC#3" in result.detail
    assert "AC#2" in result.detail
    assert "11.76" in result.detail
    assert "11.75" in result.detail
    assert result.evidence["contradictions"][0]["relation"] == "total = net + vat"


def test_the_contradiction_blocks_approval() -> None:
    """It scored 1.0 on every gate and was signed. It must not be signable now."""
    plan = _plan(LIVE_034)
    report = run_readiness(plan, _epic(plan))
    blocking = [r.check_id for r in report.unwaived_hard_failures(plan)]
    assert CHECK_ID in blocking
    assert report.is_ready(plan) is False


def test_the_logic_this_check_decides_from_is_fingerprinted() -> None:
    """#450: a fix to this parser must invalidate stored verdicts.

    The parser lives in ``checks.py``, which ``gate_revision`` already hashes.
    Extracting it to its own module without adding that module to
    ``_SOURCE_MODULES`` would freeze every verdict this check ever wrote.
    """
    assert "plan.review.readiness.checks" in _SOURCE_MODULES
    assert _stated_relations.__module__ == "plan.review.readiness.checks"
    assert _numeric_bindings.__module__ == "plan.review.readiness.checks"


def test_a_mis_parse_costs_one_waiver_not_a_re_plan() -> None:
    result = _result(LIVE_034)
    assert result.hard is True
    assert result.waivable is True


def test_the_check_runs_as_part_of_the_default_catalog() -> None:
    """Not registering it is the same as not having written it."""
    plan = _plan(LIVE_034)
    report = run_readiness(plan, _epic(plan), checks=default_checks())
    assert report.result(CHECK_ID) is not None


# ── the control: real plans that must NOT fire ────────────────────────────


def test_live_vat_quote_plans_pass() -> None:
    """027 / 029 / 030: same money shape, two worked examples each, consistent."""
    result = _result(LIVE_VAT_QUOTE)
    assert result.status == "pass", result.detail


def test_the_control_really_exercises_the_arithmetic() -> None:
    """A control that parsed no relation would prove nothing.

    ``net + vat equals total`` must be recognised, and both of the plan's worked
    examples (120.00 and 108.00) evaluated against it.
    """
    relations = [r for _, text in LIVE_VAT_QUOTE for r in _stated_relations(text)]
    assert ("total", "net", "+", "vat") in relations
    assert _numeric_bindings(LIVE_VAT_QUOTE[0][1])["total"] == pytest.approx(120.00)
    assert _numeric_bindings(LIVE_VAT_QUOTE[2][1])["total"] == pytest.approx(108.00)


def test_ambiguous_binding_is_not_a_contradiction() -> None:
    """AC2 of the control binds `total` twice (1.01 and 2.68) — that is two
    examples in one criterion, not a value. Guessing either would be a false fire.
    """
    assert "total" not in _numeric_bindings(LIVE_VAT_QUOTE[1][1])


# ── the false-positive classes the guards exist for ───────────────────────


def test_no_criteria_is_not_applicable() -> None:
    plan = _plan([])
    assert run_readiness(plan, _epic(plan)).result(CHECK_ID).status == "not_applicable"


def test_a_plan_with_no_stated_relation_passes() -> None:
    assert (
        _result(
            [
                ("AC#1", "The endpoint returns `net` 10.00, `vat` 1.75 and `total` 11.76."),
                ("AC#2", "Responses are JSON and the service starts in under 5 seconds."),
            ]
        ).status
        == "pass"
    )


def test_a_conditional_invariant_is_not_contradicted_by_an_example_outside_it() -> None:
    assert (
        _result(
            [
                ("AC#1", "When no discount applies, `total` = `net` + `vat`."),
                ("AC#2", "A discounted line returns `net` 90.00, `vat` 18.00 and `total` 100.00."),
            ]
        ).status
        == "pass"
    )


def test_mixed_precision_is_a_rounding_artifact_not_a_contradiction() -> None:
    assert (
        _result(
            [
                ("AC#1", "`total` = `net` + `vat`."),
                ("AC#2", "A line returns `net` 10.5, `vat` 1.75 and `total` 12.26."),
            ]
        ).status
        == "pass"
    )


def test_an_example_that_deliberately_states_a_violation_is_not_flagged() -> None:
    assert (
        _result(
            [
                ("AC#1", "`total` = `net` + `vat`."),
                (
                    "AC#2",
                    "A stored line whose `net` is 10.00, `vat` is 1.75 and `total` is "
                    "11.99 is rejected as invalid on import.",
                ),
            ]
        ).status
        == "pass"
    )


def test_a_thousands_separator_is_never_read_as_a_digit_group() -> None:
    """ "1,000.00" must not parse as 000.00 and manufacture a contradiction."""
    assert (
        _result(
            [
                ("AC#1", "`total` = `net` + `vat`."),
                ("AC#2", "A line returns `net` 1,000.00, `vat` 200.00 and `total` 1,200.00."),
            ]
        ).status
        == "pass"
    )


def test_a_percentage_is_not_a_money_value() -> None:
    assert (
        _result(
            [
                ("AC#1", "`total` = `net` + `vat`."),
                ("AC#2", "A line returns `net` 100%, `vat` 20% and `total` 125%."),
            ]
        ).status
        == "pass"
    )


def test_a_hyphenated_word_is_not_a_subtraction() -> None:
    """ "half-up" must not read as ``half - up``."""
    assert _stated_relations("`net` = half-up round of `unit_price`.") == []


def test_a_multiplication_rule_is_left_alone() -> None:
    """``vat = net * rate`` with a stated rounding rule is not exactly testable."""
    assert (
        _result(
            [
                ("AC#1", "`vat` = half-up round of `net` * `rate`."),
                ("AC#2", "A line returns `net` 9.99, `rate` 0.20 and `vat` 2.00."),
            ]
        ).status
        == "pass"
    )


def test_a_correct_worked_example_passes() -> None:
    assert (
        _result(
            [
                ("AC#1", "`total` = `net` + `vat`."),
                ("AC#2", "A line returns `net` 10.00, `vat` 1.75 and `total` 11.75."),
            ]
        ).status
        == "pass"
    )


def test_a_subtraction_invariant_is_checked_too() -> None:
    assert (
        _result(
            [
                ("AC#1", "`net` = `gross` - `discount`."),
                ("AC#2", "A line returns `gross` 100.00, `discount` 10.00 and `net` 91.00."),
            ]
        ).status
        == "fail"
    )


# ── the service flow: blocked, then waivable ──────────────────────────────


def _markdown_034() -> str:
    bullets = "\n".join(f"- {text}" for _, text in LIVE_034)
    return (
        "# Invoice line-total endpoint\n\n"
        "Add one FastAPI endpoint that computes the net, VAT and gross total "
        "for a single invoice line.\n\n"
        f"## Acceptance Criteria\n{bullets}\n"
    )


def _session(svc: PlanService) -> str:
    session = svc.ingest_text(_markdown_034(), title="Invoice line-total endpoint")
    session.epic = _epic(session.plan)
    review = PlanReview(plan_id=session.plan.plan_id, gates_passed=True)
    review.readiness = run_readiness(session.plan, session.epic)
    session.review = review
    return session.session_id


def test_approve_is_blocked_by_the_contradiction() -> None:
    svc = PlanService()
    with pytest.raises(ApprovalError, match=CHECK_ID):
        svc.approve(_session(svc), approver="olaf")


def test_a_human_can_waive_it() -> None:
    """The escape hatch: a mis-parse costs one audited waiver, not a re-plan."""
    svc = PlanService()
    sid = _session(svc)
    out = svc.waive(sid, check_ids=[CHECK_ID], reason="typo already fixed", waived_by="olaf")
    assert out.review.readiness.result(CHECK_ID).status == "fail"  # still recorded
    assert not [
        r.check_id
        for r in out.review.readiness.unwaived_hard_failures(out.plan)
        if r.check_id == CHECK_ID
    ]
