#!/usr/bin/env python3
"""A PR body that says it does NOT close an issue must not close it.

PFactory#520. The auto-close workflow matched a closing keyword anywhere in a
body, so PFactory#519's sentence

    It does not close #468.

closed #468 — an issue tracking a ~2700-error backlog, of which #519 cleared
72. The incentive was backwards: an author careful enough to spell out what
their change does not do was the one most likely to trip it, and the failure
was silent (issue closed, PR green, nothing reported).

Both directions are pinned here. A matcher that collected nothing would also
stop the false close, and would be worse — every fixed issue would stay open
with nobody noticing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

from closing_keywords import closing_issue_numbers  # noqa: E402


@pytest.mark.parametrize(
    "body",
    [
        "It does not close #468.",
        "**It does not close #468.**",
        "This does not close #468. Roughly 2700 errors remain.",
        "doesn't fix #468",
        "will not close #468",
        "never resolves #468",
        "does not yet close #468",
        "This PR cannot fix #468 on its own.",
    ],
)
def test_a_negated_mention_does_not_close(body: str):
    assert closing_issue_numbers(body) == [], body


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Closes #517.", [517]),
        ("closes #517", [517]),
        ("Fixes #123 and resolves #124.", [123, 124]),
        ("Fixed #1\nResolved #2\nCloses #3", [1, 2, 3]),
        ("This closes #42 as a side effect.", [42]),
    ],
)
def test_a_real_closing_keyword_still_closes(body: str, expected: list[int]):
    """The direction that must not regress: a matcher collecting nothing would
    pass every negation test above while quietly disabling the workflow."""
    assert closing_issue_numbers(body) == expected, body


def test_refs_is_not_a_closing_keyword():
    """This fleet uses `Refs` for "related to", often a partial fix."""
    assert closing_issue_numbers("Refs PFactory#466, #467") == []


def test_a_body_mixing_both_keeps_only_the_real_one():
    """The exact shape of #519: a genuine close plus a disclaimer."""
    body = "Closes #519.\n\nIt does not close #468 — roughly 2700 errors remain."
    assert closing_issue_numbers(body) == [519]


def test_duplicates_collapse_but_order_is_kept():
    assert closing_issue_numbers("Closes #9, fixes #3, closes #9") == [9, 3]


def test_the_script_runs_as_the_workflow_invokes_it():
    """The workflow pipes bodies through `python3 scripts/closing_keywords.py`.

    Importing the function proves the logic; this proves the entry point the
    workflow actually uses, which is the thing that would break silently.
    """
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(_REPO / "scripts" / "closing_keywords.py")],
        input="Closes #7.\nIt does not close #8.\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.split() == ["7"], proc.stdout
