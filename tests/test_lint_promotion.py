"""Tests for the flake-lint promotion primitive — Task 7 (#8) commit 3.

Promotion sits ON TOP of flake_risk_lint (Task 6 commit 3). It reads
the medium-severity findings and decides which ones should become
rejects based on additional source context.

These tests run the real flake_risk_lint first (because the promotion
input is its output, and we want round-trip realism), then feed the
result into promote_flake_findings and assert the decisions.

Covered:
  - High-severity hits are NOT re-classified (still rejected)
  - time_sleep with no async / no clock injection → promoted
  - time_sleep in an async test → kept medium
  - time_sleep with monkeypatch clock injection → kept medium
  - datetime.now() with no freeze import + used in assert → promoted
  - datetime.now() with freezegun import → kept medium
  - datetime.now() used only for logging (not in assert) → kept medium
  - Unknown pattern → kept medium (conservative default)
  - Syntax error in source → no decisions made
  - PromotionResult.should_reject + .summary work
"""

from __future__ import annotations

import textwrap

import pytest
from agents.flake_risk_lint import (
    FlakeRiskHit,
    FlakeRiskResult,
    flake_risk_lint,
)
from agents.lint_promotion import (
    PromotionDecision,
    PromotionResult,
    promote_flake_findings,
)

# ── time_sleep promotion ───────────────────────────────────────────────


def test_time_sleep_synchronous_test_promotes() -> None:
    source = textwrap.dedent('''
        import time

        def test_x():
            time.sleep(0.5)
            assert True
    ''').lstrip()

    result = flake_risk_lint(source)
    assert any(h.pattern == "time_sleep" for h in result.flagged)

    promo = promote_flake_findings(result, source)
    sleep_decision = next(d for d in promo.decisions if d.pattern == "time_sleep")
    assert sleep_decision.promoted is True
    assert "synchronous" in sleep_decision.reason or "flake-prone" in sleep_decision.reason
    assert promo.should_reject is True


def test_time_sleep_in_async_test_kept_medium() -> None:
    """`time.sleep` in async-using source: keep medium — may be legitimate
    yield (still antipattern but not auto-rejectable)."""
    source = textwrap.dedent('''
        import asyncio
        import time

        async def test_x():
            time.sleep(0.1)
            await asyncio.sleep(0)
    ''').lstrip()

    result = flake_risk_lint(source)
    if not any(h.pattern == "time_sleep" for h in result.flagged):
        pytest.skip("flake_risk_lint didn't flag time_sleep on this shape")

    promo = promote_flake_findings(result, source)
    sleep_decision = next(d for d in promo.decisions if d.pattern == "time_sleep")
    assert sleep_decision.promoted is False
    assert "async" in sleep_decision.reason


def test_time_sleep_with_monkeypatch_clock_kept_medium() -> None:
    source = textwrap.dedent('''
        import time

        def test_x(monkeypatch):
            monkeypatch.setattr("time.sleep", lambda s: None)
            time.sleep(5)
            assert True
    ''').lstrip()

    result = flake_risk_lint(source)
    if not any(h.pattern == "time_sleep" for h in result.flagged):
        pytest.skip("flake_risk_lint didn't flag time_sleep on this shape")

    promo = promote_flake_findings(result, source)
    sleep_decision = next(d for d in promo.decisions if d.pattern == "time_sleep")
    assert sleep_decision.promoted is False
    assert "clock injection" in sleep_decision.reason


# ── datetime.now promotion ─────────────────────────────────────────────


def test_datetime_now_in_assert_no_freeze_promotes() -> None:
    """No freeze import + datetime.now() inside an assert → promote."""
    source = textwrap.dedent('''
        from datetime import datetime

        def test_x():
            result = some_function()
            assert result.timestamp == datetime.now()
    ''').lstrip()

    result = flake_risk_lint(source)
    if not any(h.pattern == "datetime_now_no_freeze" for h in result.flagged):
        pytest.skip("flake_risk_lint didn't flag datetime.now on this shape")

    promo = promote_flake_findings(result, source)
    dt_decision = next(d for d in promo.decisions if d.pattern == "datetime_now_no_freeze")
    assert dt_decision.promoted is True
    assert "assert" in dt_decision.reason
    assert promo.should_reject is True


def test_datetime_now_with_freezegun_kept_medium() -> None:
    """Even if datetime.now is flagged, freezegun import → keep medium."""
    source = textwrap.dedent('''
        from datetime import datetime
        from freezegun import freeze_time

        @freeze_time("2026-01-01")
        def test_x():
            assert datetime.now().year == 2026
    ''').lstrip()

    result = flake_risk_lint(source)
    # Construct a synthetic medium for this case — flake_risk_lint
    # bypasses the datetime check when freeze imports are present, so
    # we can't get a real flagged hit here. Force one to verify the
    # promotion rule still defends.
    forced = FlakeRiskResult(
        ok=True,
        hits=[FlakeRiskHit(
            pattern="datetime_now_no_freeze",
            severity="medium",
            lineno=5,
            detail="forced",
            snippet="assert datetime.now().year == 2026",
        )],
    )
    promo = promote_flake_findings(forced, source)
    dt_decision = next(d for d in promo.decisions if d.pattern == "datetime_now_no_freeze")
    assert dt_decision.promoted is False
    assert "freezing" in dt_decision.reason


def test_datetime_now_used_only_for_logging_kept_medium() -> None:
    """datetime.now() outside any assert → keep medium (probably logging)."""
    source = textwrap.dedent('''
        from datetime import datetime

        def test_x():
            t = datetime.now()
            print(f"started at {t}")
            assert True
    ''').lstrip()

    result = flake_risk_lint(source)
    if not any(h.pattern == "datetime_now_no_freeze" for h in result.flagged):
        pytest.skip("flake_risk_lint didn't flag this shape")

    promo = promote_flake_findings(result, source)
    dt_decision = next(d for d in promo.decisions if d.pattern == "datetime_now_no_freeze")
    assert dt_decision.promoted is False
    assert "not used in assertions" in dt_decision.reason


# ── High-severity unaffected ───────────────────────────────────────────


def test_high_severity_findings_pass_through_untouched() -> None:
    """A source with a HIGH finding (dict-iteration-order) gets its
    high_count surfaced but no promotion happens."""
    source = textwrap.dedent('''
        def test_x():
            d = {"a": 1, "b": 2}
            assert list(d.keys()) == ["a", "b"]
    ''').lstrip()

    result = flake_risk_lint(source)
    assert any(h.severity == "high" for h in result.hits)

    promo = promote_flake_findings(result, source)
    assert promo.high_count >= 1
    assert promo.medium_count == 0
    assert promo.promoted_count == 0
    assert promo.should_reject is True  # via high_count, not promotion


# ── Defensive paths ────────────────────────────────────────────────────


def test_unknown_pattern_kept_medium() -> None:
    """An unrecognised medium pattern defaults to keep-medium."""
    forced = FlakeRiskResult(
        ok=True,
        hits=[FlakeRiskHit(
            pattern="some_future_pattern",
            severity="medium",
            lineno=10,
            detail="forced",
            snippet="x",
        )],
    )
    promo = promote_flake_findings(forced, "def test_x(): pass\n")
    decision = promo.decisions[0]
    assert decision.promoted is False
    assert "no promotion rule" in decision.reason


def test_syntax_error_source_keeps_all_medium() -> None:
    """If the source has a syntax error we can't reason about context.
    Don't mass-promote; surface high_count but keep decisions empty."""
    forced = FlakeRiskResult(
        ok=False,
        hits=[FlakeRiskHit(
            pattern="time_sleep", severity="medium", lineno=1, detail="x",
        )],
        syntax_error="invalid syntax at line 1",
    )
    promo = promote_flake_findings(forced, "def(:\n")
    assert promo.decisions == []
    assert promo.high_count == 0
    assert promo.medium_count == 1
    assert promo.promoted_count == 0
    assert promo.should_reject is False  # no signal to act on


def test_promotion_rule_exception_handled() -> None:
    """If a promotion rule somehow raises, keep medium with the error
    captured. (Conservative, doesn't crash the evaluator.)"""
    import agents.lint_promotion as mod

    def _bomb(hit, source):
        raise RuntimeError("rule explosion")

    forced = FlakeRiskResult(
        ok=True,
        hits=[FlakeRiskHit(
            pattern="time_sleep", severity="medium", lineno=3,
            detail="x", snippet="time.sleep(1)",
        )],
    )
    original = mod._PROMOTION_RULES["time_sleep"]
    mod._PROMOTION_RULES["time_sleep"] = _bomb
    try:
        promo = promote_flake_findings(forced, "import time\ntime.sleep(1)\n")
        decision = promo.decisions[0]
        assert decision.promoted is False
        assert "rule errored" in decision.reason
        assert "RuntimeError" in decision.reason
    finally:
        mod._PROMOTION_RULES["time_sleep"] = original


# ── PromotionResult helpers ────────────────────────────────────────────


def test_summary_with_high_and_promoted() -> None:
    source = textwrap.dedent('''
        import time

        def test_x():
            d = {"a": 1}
            time.sleep(1)
            assert list(d.keys()) == ["a"]
    ''').lstrip()
    result = flake_risk_lint(source)
    promo = promote_flake_findings(result, source)
    summary = promo.summary()
    if promo.high_count:
        assert "high (rejected)" in summary
    if promo.promoted_count:
        assert "promoted" in summary


def test_summary_with_no_findings() -> None:
    forced = FlakeRiskResult(ok=True, hits=[])
    promo = promote_flake_findings(forced, "def test_x(): pass\n")
    assert promo.summary() == "no findings"


def test_decision_label() -> None:
    d_promote = PromotionDecision(
        pattern="x", lineno=1, promoted=True, reason="r",
    )
    d_keep = PromotionDecision(
        pattern="x", lineno=2, promoted=False, reason="r",
    )
    assert d_promote.label == "promote"
    assert d_keep.label == "keep_medium"


def test_should_reject_false_when_no_findings() -> None:
    forced = FlakeRiskResult(ok=True, hits=[])
    promo = promote_flake_findings(forced, "")
    assert promo.should_reject is False
    assert promo.high_count == 0
    assert promo.medium_count == 0
    assert promo.promoted_count == 0
