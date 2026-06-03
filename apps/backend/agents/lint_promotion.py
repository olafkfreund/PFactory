"""Flake-lint promotion — Task 7 (#8) commit 3.

The fifth of the FIVE evaluation signals (the others: coverage delta,
3× stability, mutate-and-check, LLM semantic relevance).

Task 6's ``flake_risk_lint`` already classifies findings as:
  - ``severity='high'``  — auto-rejected at generation time
  - ``severity='medium'`` — *flagged*, deferred to the Evaluator

This module decides which medium-severity findings get *promoted* to
rejects based on additional source-context signals the bare AST
visitor doesn't have. Examples:

  - ``time.sleep(0.1)`` is medium by default, but PROMOTE if the test
    isn't doing async / no event loop / no clock injection.
  - ``datetime.now()`` is medium by default, but PROMOTE if there's
    no ``freezegun`` / ``time_machine`` import AND the assertion
    references the result directly (vs. just logging it).

The Evaluator commit-5 wiring will:
  1. Run ``flake_risk_lint(source)`` (already happens at generation
     time in gen_functional commit 3 — Evaluator re-runs to get the
     full ``flagged`` list).
  2. Call ``promote_flake_findings(result, source)`` to get a
     ``PromotionResult`` deciding which mediums become rejects.
  3. Pass the verdict into the verdicts.json blob.

Promotion logic is **conservative**: when in doubt, keep the medium
medium. We promote only when we have a positive signal that the
flag is going to cause real flake in CI.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# We import from flake_risk_lint to type-annotate, but no runtime
# dependency on its internals — just the public dataclasses.
from agents.flake_risk_lint import FlakeRiskHit, FlakeRiskResult


@dataclass(frozen=True)
class PromotionDecision:
    """One promotion decision per medium-severity FlakeRiskHit."""

    pattern: str          # e.g., "time_sleep", "datetime_now_no_freeze"
    lineno: int
    promoted: bool        # True if this medium should be treated as a reject
    reason: str           # short human-readable justification

    @property
    def label(self) -> str:
        return "promote" if self.promoted else "keep_medium"


@dataclass(frozen=True)
class PromotionResult:
    """Aggregate result of promoting a FlakeRiskResult.

    ``decisions`` parallels the input's ``flagged`` list (medium hits).
    ``promoted_count`` is the convenience count of decisions where
    ``promoted=True``.

    If ``input_result.rejected`` was non-empty (existing high findings),
    those are NOT re-classified — they stay rejected. Promotion only
    operates on the medium subset.
    """

    decisions: list[PromotionDecision] = field(default_factory=list)
    high_count: int = 0           # pre-existing high-severity hits
    medium_count: int = 0         # input medium hits
    promoted_count: int = 0       # decisions where promoted=True

    @property
    def should_reject(self) -> bool:
        """The test should be rejected if there was any pre-existing
        ``high`` finding OR if any medium was promoted to reject."""
        return self.high_count > 0 or self.promoted_count > 0

    def summary(self) -> str:
        bits = []
        if self.high_count:
            bits.append(f"{self.high_count} high (rejected)")
        if self.promoted_count:
            bits.append(f"{self.promoted_count} promoted")
        keep = self.medium_count - self.promoted_count
        if keep > 0:
            bits.append(f"{keep} kept medium")
        return ", ".join(bits) or "no findings"


# ─── Source-context signals ─────────────────────────────────────────────


_FREEZE_HINTS = ("freezegun", "freeze_time", "time_machine", "pytest_freezer")
_ASYNC_HINTS = ("asyncio", "anyio", "trio", "async def", "await ")
_CLOCK_INJECTION_HINTS = (
    "monkeypatch", "freeze_time", "fake_clock", "Clock(", "mock_clock",
)


def _has_any(source: str, needles: tuple[str, ...]) -> bool:
    return any(n in source for n in needles)


def _has_freeze_context(source: str) -> bool:
    """True if the source imports / uses a time-freezing helper."""
    return _has_any(source, _FREEZE_HINTS)


def _has_async_context(source: str) -> bool:
    """True if the test file uses asyncio / anyio / trio / async def."""
    return _has_any(source, _ASYNC_HINTS)


def _has_clock_injection(source: str) -> bool:
    """True if the test injects a clock via monkeypatch or a fake."""
    return _has_any(source, _CLOCK_INJECTION_HINTS)


def _datetime_now_used_in_assertion(source: str) -> bool:
    """Walk the AST to see if datetime.now() / utcnow() is referenced
    INSIDE an ``assert`` statement (vs. just logged/printed)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.found = False
            self._in_assert = False

        def visit_Assert(self, node: ast.Assert) -> None:
            prev = self._in_assert
            self._in_assert = True
            self.generic_visit(node)
            self._in_assert = prev

        def visit_Call(self, node: ast.Call) -> None:
            if self._in_assert and self._is_datetime_now(node):
                self.found = True
            self.generic_visit(node)

        @staticmethod
        def _is_datetime_now(call: ast.Call) -> bool:
            func = call.func
            if isinstance(func, ast.Attribute):
                return func.attr in ("now", "utcnow")
            return False

    v = _Visitor()
    v.visit(tree)
    return v.found


# ─── Per-pattern promotion rules ───────────────────────────────────────
#
# Each rule takes (hit, source) → (promote: bool, reason: str).
# Conservative bias: default to NOT promoting unless the source
# context positively suggests CI flake risk.


def _promote_time_sleep(hit: FlakeRiskHit, source: str) -> tuple[bool, str]:
    """``time.sleep(...)`` → promote unless the test is async (where
    sleep is sometimes used legitimately to yield to the event loop,
    though even there it's an antipattern) OR the test injects a clock.
    """
    if _has_async_context(source):
        return False, "async context — sleep may be intentional yield"
    if _has_clock_injection(source):
        return False, "clock injection present — sleep is shadowed"
    return True, "synchronous test with bare time.sleep() — flake-prone"


def _promote_datetime_now(hit: FlakeRiskHit, source: str) -> tuple[bool, str]:
    """``datetime.now()`` / ``utcnow()`` → promote when:
      - no freeze import is present, AND
      - the call appears INSIDE an assert (assertion depends on
        wall-clock time, which is the textbook flake recipe).
    Keep medium otherwise (e.g., just used for logging).
    """
    if _has_freeze_context(source):
        return False, "time freezing imported — datetime.now is frozen"
    if _datetime_now_used_in_assertion(source):
        return True, (
            "datetime.now() referenced inside assert without freeze "
            "— assertion depends on wall-clock time"
        )
    return False, "datetime.now() not used in assertions — keep flagged"


_PROMOTION_RULES = {
    "time_sleep": _promote_time_sleep,
    "datetime_now_no_freeze": _promote_datetime_now,
}


# ─── Public entrypoint ──────────────────────────────────────────────────


def promote_flake_findings(
    result: FlakeRiskResult,
    source: str,
) -> PromotionResult:
    """Decide which medium-severity flake-lint findings should be
    promoted to rejects.

    Args:
        result: The output of ``flake_risk_lint(source)``.
        source: The same source the result was computed from. Required
            because the promotion rules look at additional context
            (imports, async usage, assert-vs-log placement) the bare
            AST visitor doesn't capture.

    Returns:
        PromotionResult with one decision per medium hit, plus the
        aggregate ``should_reject`` convenience.

    Notes:
        - ``high`` findings are NOT re-classified — they stay rejected
          (reflected in the ``high_count`` field).
        - Unknown medium patterns default to NOT promoted (with a
          conservative reason). This makes adding new lint patterns
          downstream safer.
    """
    if result.syntax_error:
        # The source didn't parse; we can't reason about context.
        # Keep all mediums as-is rather than mass-promote.
        return PromotionResult(
            decisions=[],
            high_count=len(result.rejected),
            medium_count=len(result.flagged),
            promoted_count=0,
        )

    decisions: list[PromotionDecision] = []
    promoted_count = 0

    for hit in result.flagged:
        rule = _PROMOTION_RULES.get(hit.pattern)
        if rule is None:
            decisions.append(PromotionDecision(
                pattern=hit.pattern,
                lineno=hit.lineno,
                promoted=False,
                reason="no promotion rule registered for this pattern",
            ))
            continue
        try:
            promote, reason = rule(hit, source)
        except Exception as exc:  # noqa: BLE001 — defensive
            decisions.append(PromotionDecision(
                pattern=hit.pattern,
                lineno=hit.lineno,
                promoted=False,
                reason=f"rule errored: {type(exc).__name__}: {exc}",
            ))
            continue
        decisions.append(PromotionDecision(
            pattern=hit.pattern,
            lineno=hit.lineno,
            promoted=promote,
            reason=reason,
        ))
        if promote:
            promoted_count += 1

    return PromotionResult(
        decisions=decisions,
        high_count=len(result.rejected),
        medium_count=len(result.flagged),
        promoted_count=promoted_count,
    )
