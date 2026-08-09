#!/usr/bin/env python3
"""Every excluded stock rule must have a custom query that replaces it.

PFactory#517. ``codeql-config.yml`` excludes a stock rule whenever this repo
ships a barrier-aware variant, because the stock query cannot follow a
validator imported from ``services/`` or one that establishes safety by
raising. That trade is fine while both halves are present.

The failure mode is losing one half. Delete the custom query, or rename its
``@id``, or let the pack fail to resolve, and the exclude stays — so the rule
is checked by nothing and CodeQL still reports success. That is the same shape
as PFactory#455, where a linter that never ran read as "no violations": the
absence of a finding is indistinguishable from a clean result.

A real fixture run would be stronger, but it needs a CodeQL database and
several minutes; this catches the specific way the pair comes apart, in
milliseconds, on every commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CONFIG = _REPO / ".github" / "codeql" / "codeql-config.yml"
_QUERY_DIR = _REPO / ".github" / "codeql" / "custom-queries"


def _excluded_rule_ids() -> list[str]:
    """Rule ids under ``query-filters: - exclude: id: ...``."""
    text = _CONFIG.read_text(encoding="utf-8")
    return re.findall(r"^\s*id:\s*(\S+)\s*$", text, flags=re.MULTILINE)


def _query_ids() -> dict[str, Path]:
    """``@id`` of every .ql in the custom pack, mapped to its file."""
    out: dict[str, Path] = {}
    for ql in sorted(_QUERY_DIR.glob("*.ql")):
        m = re.search(r"^\s*\*\s*@id\s+(\S+)\s*$", ql.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            out[m.group(1)] = ql
    return out


def test_the_config_and_the_pack_both_exist():
    """A missing file would make every other assertion here vacuous."""
    assert _CONFIG.is_file(), _CONFIG
    assert _QUERY_DIR.is_dir(), _QUERY_DIR
    assert list(_QUERY_DIR.glob("*.ql")), "custom pack has no queries"


def test_every_exclude_has_a_replacement_query():
    """An excluded rule with no replacement is a rule nothing checks."""
    queries = _query_ids()
    missing = [rule_id for rule_id in _excluded_rule_ids() if f"{rule_id}-sanitized" not in queries]
    assert not missing, (
        f"excluded with no -sanitized replacement: {missing}. Present query ids: {sorted(queries)}"
    )


def test_the_excludes_are_the_ones_we_expect():
    """Pin the list, so adding an exclude is a deliberate, reviewed act.

    Without this, `test_every_exclude_has_a_replacement_query` would happily
    accept a new exclude that arrived with a barrier written to silence a real
    finding — the pairing would hold and nobody would look.
    """
    assert sorted(_excluded_rule_ids()) == [
        "py/command-line-injection",
        "py/full-ssrf",
        "py/path-injection",
    ]


@pytest.mark.parametrize(
    "rule_id", ["py/path-injection", "py/command-line-injection", "py/full-ssrf"]
)
def test_each_replacement_query_documents_why(rule_id: str):
    """A barrier is a security claim, so it has to say what it relies on.

    Cheap to satisfy and cheap to check, but it is the difference between a
    reviewer seeing the reasoning and seeing only a list of function names.
    """
    ql = _query_ids()[f"{rule_id}-sanitized"]
    body = ql.read_text(encoding="utf-8")
    assert "sanitiz" in body.lower() or "barrier" in body.lower(), ql
    # The class must carry a doc comment, not just the query header.
    assert re.search(r"/\*\*.*?\*/\s*class \w+ extends", body, re.S), (
        f"{ql.name}: the Sanitizer class has no doc comment explaining the claim"
    )
