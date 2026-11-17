"""String work-item identifier (RFC-0022 Phase 1, Factory#3774), PFactory edge.

Providers now carry ``IssueData.number`` as ``str``. PFactory still stores and
serves integers (column migration is a later phase), so the edge converts with
``as_issue_int`` and rejects a non-numeric key. These tests pin that contract.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
for _p in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from plan.completion import origin_key  # noqa: E402
from runners.github.providers.protocol import as_issue_int  # noqa: E402


def test_origin_key_parity_int_and_str() -> None:
    assert origin_key("o/r", 5) == origin_key("o/r", "5") == "o/r#5"


def test_non_numeric_key_rejected_at_edge() -> None:
    assert as_issue_int("5") == 5
    with pytest.raises(ValueError, match="not numeric"):
        as_issue_int("PROJ-1")


# `<provider issue>.number == <int>` / `in <int set>` silently never matches a str.
_SERVER = _ROOT / "apps" / "web-server" / "server"
_BAD = re.compile(r"\b(?:iss|issue|issue_obj)\.number\s*(?:==|!=|in\b|not in\b)")


def test_no_int_comparison_against_provider_issue_number() -> None:
    hits = [
        f"{p.relative_to(_ROOT)}:{i}"
        for p in _SERVER.rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if _BAD.search(line)
    ]
    assert not hits, hits
