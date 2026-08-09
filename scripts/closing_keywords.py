#!/usr/bin/env python3
"""Issue numbers a PR body actually asks to close.

Extracted from .github/workflows/auto-close-issues.yml (PFactory#520) so the
workflow and its tests share one implementation. Inline shell in a workflow
cannot be unit-tested, and a test that restates the regex tests a copy — which
drifts from the thing that runs.

The rule the workflow needs is narrower than "does the body mention a closing
keyword". A PR body that says what the change does NOT do is a body written
with care, and it was closing issues:

    PFactory#519 said "It does not close #468."
    #468 was closed on merge. It tracks a ~2700-error backlog.

Reads stdin, writes one issue number per line.
"""

from __future__ import annotations

import re
import sys

# `Refs` is deliberately absent: this fleet uses it to mean "related to",
# usually for a partial fix, and treating it as closing would close issues that
# are only partly addressed.
_KEYWORD = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"

# A closing keyword whose issue number we should collect.
_CLOSING = re.compile(rf"\b{_KEYWORD}\b\s*#(\d+)", re.IGNORECASE)

# A negated one, neutralised before the pass above runs. The optional word in
# the middle covers "does not yet close #N" / "will not fully fix #N" without
# swallowing a whole sentence.
_NEGATED = re.compile(
    r"\b(?:does not|do not|doesn't|don't|did not|didn't|will not|won't|"
    r"cannot|can't|never|not)\b(?:\s+\w+)?\s+" + _KEYWORD + r"\b",
    re.IGNORECASE,
)


def closing_issue_numbers(text: str) -> list[int]:
    """Issue numbers the text asks to close, negations excluded.

    Order-preserving and de-duplicated, so the caller's log reads in the order
    the author wrote them.
    """
    neutralised = _NEGATED.sub("NEGATED", text or "")
    seen: dict[int, None] = {}
    for m in _CLOSING.finditer(neutralised):
        seen.setdefault(int(m.group(1)), None)
    return list(seen)


def main() -> int:
    for n in closing_issue_numbers(sys.stdin.read()):
        # Printing IS this script's interface: the workflow pipes stdout
        # into `sort -u`.
        print(n)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
