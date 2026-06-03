"""Flake-risk lint for Gen-Functional — Task 6 (#7) commit 3.

LLM-generated tests are slightly flakier than human-written ones — the
research cited in the design plan blames a few specific anti-patterns:

  - assertions that rely on dict iteration order
  - assertions that rely on set iteration order
  - `time.sleep` for synchronisation in tests
  - `datetime.now()` / `datetime.utcnow()` without a freezer
  - `random` calls without a seed

This module is a focused AST scanner for those five. Sibling to
``preflight_static.py``; same dataclass + summary shape, no subprocess
work needed.

Severity model:

  high  — REJECT. Gen-Functional (commit 5) will treat the file as
          rejected and trigger a Planner replan.
  medium — FLAG. The Evaluator (Task 7, #8) decides whether to accept
          or downgrade the verdict based on the rest of the signal
          (mutation kills, stability across 3 reruns, etc.).

Per the design plan:
  dict-iteration-order → high
  set-iteration-order  → high
  random-no-seed       → high
  time.sleep           → medium
  datetime.now-no-freeze → medium

Patterns + heuristics (all AST-only, no project context required):

  1. dict_iteration_order
     `assert list(d.keys()) == [...]`,
     `assert list(d.items()) == [...]`,
     `assert list(d.values()) == [...]`,
     and the same with `tuple(...)`. Also direct `d.keys() == [...]`
     (no list() wrapper) since dict_keys objects can compare to lists
     via element-by-element under some equality paths.

  2. set_iteration_order
     `assert list(<set_literal>) == [...]` or
     `assert list(s) == [...]` where ``s`` is bound to a set literal
     in the same scope (limited scope tracking — keep it cheap).
     We miss the case where ``s`` is bound via a function return or
     deeper expression; downstream stability re-runs catch those.

  3. time_sleep
     Any call to ``time.sleep(...)`` (Attribute access on Name 'time')
     or bare ``sleep(...)`` where ``from time import sleep`` is in
     the file. The latter is the more common shape in LLM output.

  4. datetime_now_no_freeze
     Any call to ``datetime.now(...)`` / ``datetime.utcnow(...)`` /
     ``datetime.datetime.now(...)`` AND no freezegun / time-machine /
     freeze_time / time_machine import in the file.

  5. random_no_seed
     Any call to ``random.<choice|randint|random|sample|shuffle|
     uniform|getrandbits|...>`` AND no ``random.seed(...)`` call in
     the file. ``random.SystemRandom`` is explicitly allowed (used for
     security entropy; deterministic seeding doesn't apply).

The scan is intentionally cheap — no symbol tables, no type
inference. False positives are acceptable when they catch LLM
sloppiness; we'd rather over-reject than ship a flaky test.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ─── Result types ────────────────────────────────────────────────────────


@dataclass
class FlakeRiskHit:
    """A single flake-risk pattern occurrence."""

    pattern: str          # one of the 5 pattern keys below
    severity: str         # 'high' or 'medium'
    lineno: int
    detail: str           # short human-readable explanation
    snippet: str = ""     # the offending source line (best effort)


@dataclass
class FlakeRiskResult:
    """Aggregate scan outcome."""

    ok: bool              # True only if no 'high' severity hits
    hits: list[FlakeRiskHit] = field(default_factory=list)
    syntax_error: str | None = None

    @property
    def rejected(self) -> list[FlakeRiskHit]:
        return [h for h in self.hits if h.severity == "high"]

    @property
    def flagged(self) -> list[FlakeRiskHit]:
        return [h for h in self.hits if h.severity == "medium"]

    def summary(self) -> str:
        if self.syntax_error:
            return f"syntax error: {self.syntax_error}"
        if not self.hits:
            return "OK (no flake-risk patterns detected)"
        bits = []
        if self.rejected:
            bits.append(f"{len(self.rejected)} reject")
        if self.flagged:
            bits.append(f"{len(self.flagged)} flag")
        return ", ".join(bits)


# Severity table — single source of truth.
_PATTERN_SEVERITY = {
    "dict_iteration_order": "high",
    "set_iteration_order": "high",
    "random_no_seed": "high",
    "time_sleep": "medium",
    "datetime_now_no_freeze": "medium",
}

# Imports/symbols that signal "the test froze time" — presence of any of
# these in the source bypasses the datetime_now check.
_FREEZE_IMPORTS = frozenset({
    "freezegun",
    "freeze_time",
    "time_machine",
    "freezer",     # pytest-freezer
})

# Imports/symbols that signal "the test seeded random" — presence bypasses
# the random_no_seed check. A direct call to random.seed() is also a bypass.
_SEED_HINTS = frozenset({
    "pytest_randomly",   # pytest plugin that auto-seeds
})

# random.* methods that consume entropy (we flag these without a seed).
_RANDOM_ENTROPY_METHODS = frozenset({
    "random", "uniform", "randint", "randrange", "choice", "choices",
    "sample", "shuffle", "getrandbits", "betavariate", "expovariate",
    "gammavariate", "gauss", "lognormvariate", "normalvariate",
    "paretovariate", "triangular", "vonmisesvariate", "weibullvariate",
})


# ─── The visitor ─────────────────────────────────────────────────────────


class _FlakeRiskVisitor(ast.NodeVisitor):
    """Single-pass AST walker that collects FlakeRiskHits."""

    def __init__(self, source_lines: list[str]) -> None:
        self.source_lines = source_lines
        self.hits: list[FlakeRiskHit] = []

        # File-level state: collected during a pre-pass before visiting.
        # Set externally by the caller for now (see flake_risk_lint).
        self.has_freeze_import = False
        self.has_random_seed_call = False
        # Names bound to set literals in the current scope (rough scope tracking).
        self.set_bindings: set[str] = set()

    # ─── helpers ─────────────────────────────────────────────────────────

    def _snippet(self, lineno: int) -> str:
        idx = lineno - 1
        if 0 <= idx < len(self.source_lines):
            return self.source_lines[idx].strip()[:120]
        return ""

    def _hit(self, pattern: str, lineno: int, detail: str) -> None:
        self.hits.append(FlakeRiskHit(
            pattern=pattern,
            severity=_PATTERN_SEVERITY[pattern],
            lineno=lineno,
            detail=detail,
            snippet=self._snippet(lineno),
        ))

    # ─── Pattern 1+2: dict / set iteration order ─────────────────────────

    def visit_Assert(self, node: ast.Assert) -> None:
        test = node.test
        # We only care about `<lhs> == <rhs>` style assertions.
        if isinstance(test, ast.Compare) and any(isinstance(op, ast.Eq) for op in test.ops):
            self._check_compare_for_dict_set_order(test, node.lineno)
        self.generic_visit(node)

    def _check_compare_for_dict_set_order(self, node: ast.Compare, lineno: int) -> None:
        # Look at both sides of `a == b` for an iteration-order risk.
        operands = [node.left, *node.comparators]
        # Filter to (lhs, rhs) pairs joined by ==
        for i, op in enumerate(node.ops):
            if not isinstance(op, ast.Eq):
                continue
            lhs, rhs = operands[i], operands[i + 1]
            for side, other in ((lhs, rhs), (rhs, lhs)):
                self._maybe_flag_dict_keys(side, lineno)
                self._maybe_flag_dict_via_list_call(side, other, lineno)

    def _maybe_flag_dict_keys(self, node: ast.AST, lineno: int) -> None:
        """`d.keys() == ...` / `d.items() == ...` / `d.values() == ...`"""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in ("keys", "items", "values") and not node.args:
                self._hit(
                    "dict_iteration_order",
                    lineno,
                    f".{method}() comparison relies on insertion order — "
                    f"sort both sides or use sets",
                )

    def _maybe_flag_dict_via_list_call(
        self, side: ast.AST, other: ast.AST, lineno: int
    ) -> None:
        """`list(d) == [...]`, `list(d.keys()) == [...]`, `tuple(s) == (...)` etc."""
        if not isinstance(side, ast.Call):
            return
        func = side.func
        if not (isinstance(func, ast.Name) and func.id in ("list", "tuple")):
            return
        if not side.args:
            return
        arg = side.args[0]
        # Case A: list(d.keys()) / list(d.items()) / list(d.values())
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
            if arg.func.attr in ("keys", "items", "values"):
                self._hit(
                    "dict_iteration_order",
                    lineno,
                    f"list({arg.func.attr}()) comparison is iteration-order-dependent",
                )
                return
        # Case B: list({...set_literal...})
        if isinstance(arg, ast.Set):
            self._hit(
                "set_iteration_order",
                lineno,
                "list(set-literal) comparison is iteration-order-dependent — "
                "use sorted() or compare as a set",
            )
            return
        # Case C: list(VAR) where VAR is bound to a set literal in this scope
        if isinstance(arg, ast.Name) and arg.id in self.set_bindings:
            self._hit(
                "set_iteration_order",
                lineno,
                f"list({arg.id}) where {arg.id} is a set — iteration-order-dependent",
            )

    # ─── Track set bindings for the heuristic above ──────────────────────

    def visit_Assign(self, node: ast.Assign) -> None:
        # Record `name = {literal, set, here}` so subsequent
        # list(name) == [...] assertions can be caught.
        if isinstance(node.value, ast.Set):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.set_bindings.add(target.id)
        # Bonus: catch `x = {1, 2, 3}` then `assert list(x) == [1, 2, 3]`
        self.generic_visit(node)

    # ─── Pattern 3: time.sleep ───────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        self._maybe_flag_time_sleep(node)
        self._maybe_flag_datetime_now(node)
        self._maybe_flag_random_no_seed(node)
        self.generic_visit(node)

    def _maybe_flag_time_sleep(self, node: ast.Call) -> None:
        func = node.func
        # `time.sleep(...)`
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "time"
            and func.attr == "sleep"
        ):
            self._hit(
                "time_sleep",
                node.lineno,
                "time.sleep in tests is flaky — use a clock fixture or async waits",
            )
            return
        # bare `sleep(...)` (assumes `from time import sleep` in the file;
        # the visitor flags optimistically — false positives are acceptable
        # at flag-level severity).
        if isinstance(func, ast.Name) and func.id == "sleep":
            self._hit(
                "time_sleep",
                node.lineno,
                "bare sleep() in tests is flaky — use a clock fixture or async waits",
            )

    # ─── Pattern 4: datetime.now() / utcnow() without freeze ──────────────

    def _maybe_flag_datetime_now(self, node: ast.Call) -> None:
        if self.has_freeze_import:
            return
        func = node.func
        # `datetime.now(...)` / `datetime.utcnow(...)`
        if isinstance(func, ast.Attribute) and func.attr in ("now", "utcnow"):
            chain = func.value
            # Accept Name('datetime') OR Attribute(datetime, datetime) i.e.
            # `datetime.datetime.now()`.
            is_datetime_chain = (
                (isinstance(chain, ast.Name) and chain.id == "datetime")
                or (
                    isinstance(chain, ast.Attribute)
                    and isinstance(chain.value, ast.Name)
                    and chain.value.id == "datetime"
                    and chain.attr == "datetime"
                )
            )
            if is_datetime_chain:
                self._hit(
                    "datetime_now_no_freeze",
                    node.lineno,
                    f"datetime.{func.attr}() without freezegun / time_machine — "
                    f"add @freeze_time / time_machine.travel for determinism",
                )

    # ─── Pattern 5: random.* without seed ────────────────────────────────

    def _maybe_flag_random_no_seed(self, node: ast.Call) -> None:
        if self.has_random_seed_call:
            return
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "random"
            and func.attr in _RANDOM_ENTROPY_METHODS
        ):
            self._hit(
                "random_no_seed",
                node.lineno,
                f"random.{func.attr}() without random.seed(...) — "
                f"add a fixed seed in setUp / fixture for determinism",
            )


# ─── File-level pre-pass + entry point ──────────────────────────────────


def _has_freeze_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FREEZE_IMPORTS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in _FREEZE_IMPORTS:
                return True
            for alias in node.names:
                if alias.name in _FREEZE_IMPORTS:
                    return True
    return False


def _has_random_seed_call(tree: ast.AST) -> bool:
    """True if any `random.seed(...)` appears in the file OR if
    pytest_randomly is imported (it auto-seeds globally)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "random"
                and func.attr == "seed"
            ):
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names_module = (
                getattr(node, "module", None) or ""
            ).split(".")[0]
            if names_module in _SEED_HINTS:
                return True
            for alias in getattr(node, "names", []):
                if (alias.name or "").split(".")[0] in _SEED_HINTS:
                    return True
    return False


def flake_risk_lint(source: str) -> FlakeRiskResult:
    """Scan ``source`` for the five flake-risk patterns.

    Returns a FlakeRiskResult. ``.ok`` is True only when no high-
    severity hits were recorded; flagged hits don't block.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return FlakeRiskResult(
            ok=False,
            hits=[],
            syntax_error=f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})",
        )

    source_lines = source.splitlines()
    visitor = _FlakeRiskVisitor(source_lines)
    visitor.has_freeze_import = _has_freeze_import(tree)
    visitor.has_random_seed_call = _has_random_seed_call(tree)
    visitor.visit(tree)

    high = any(h.severity == "high" for h in visitor.hits)
    return FlakeRiskResult(ok=not high, hits=visitor.hits)
