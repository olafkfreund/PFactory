"""Governance query CLI tests (#1961 step 4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.governance_cli import MATCH_KEYS, format_result, main, scan, select  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "governance"


def test_scan_counts_all_four_fixture_rows():
    rows, counts = scan(FIXTURES)
    assert counts == {
        "scanned": 4,
        "signed": 3,
        "schema1": 1,
        "unsigned": 1,
    }
    assert len(rows) == 3


def test_control_filter_matches_only_schema2_match_and_prints_exact_count_line(capsys):
    rc = main(["--root", str(FIXTURES), "--control", "change-mgmt-sod"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert "pf-005-payments-refund-api" in lines[0]
    assert "Payments \u2014 Refund API" in lines[0]
    assert "autonomy_tier=hard" in lines[0]
    assert "autonomy_verdict=approval" in lines[0]
    assert "approved_by=olafkfreund" in lines[0]
    assert "risk_class=high" in lines[0]
    assert (
        lines[-1]
        == "scanned 4 rows, 3 with a signature, 1 schema-1, 1 without a signature, 1 matched"
    )


def test_tier_filter_selects_matching_schema2_row_and_ignores_schema1():
    rows, _ = scan(FIXTURES)
    hard = select(rows, tier="hard")
    assert [m["correlation_key"] for m in hard] == ["pf-005-payments-refund-api"]

    low = select(rows, tier="low")
    assert [m["correlation_key"] for m in low] == ["pf-006-docs-typo-fix"]

    unknown = select(rows, tier="unknown")
    assert unknown == []


def test_since_filter_compares_iso_date_prefix_and_excludes_schema1():
    rows, _ = scan(FIXTURES)
    oct_only = select(rows, since="2026-10-01")
    assert [m["correlation_key"] for m in oct_only] == ["pf-005-payments-refund-api"]

    since_sept = select(rows, since="2026-09-01")
    assert [m["correlation_key"] for m in since_sept] == [
        "pf-005-payments-refund-api",
        "pf-006-docs-typo-fix",
    ]


def test_json_output_emits_matches_and_counts(capsys):
    rc = main(
        [
            "--root",
            str(FIXTURES),
            "--control",
            "change-mgmt-sod",
            "--tier",
            "hard",
            "--since",
            "2026-10-14",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"] == {
        "scanned": 4,
        "signed": 3,
        "schema1": 1,
        "unsigned": 1,
        "matched": 1,
    }
    assert len(payload["matches"]) == 1
    match = payload["matches"][0]
    assert tuple(match.keys()) == tuple(sorted(MATCH_KEYS)) or set(match.keys()) == set(
        MATCH_KEYS
    )
    assert match == {
        "correlation_key": "pf-005-payments-refund-api",
        "title": "Payments \u2014 Refund API",
        "autonomy_tier": "hard",
        "autonomy_verdict": "approval",
        "approved_by": "olafkfreund",
        "approved_at": "2026-10-14T11:55:00+00:00",
        "originator": None,
        "risk_class": "high",
        "created_at": "2026-10-14T12:00:00+00:00",
    }


def test_malformed_rows_and_missing_files_are_counted_never_raised(tmp_path):
    (tmp_path / "corrupt.run.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "list.run.json").write_text("[1, 2, 3]", encoding="utf-8")
    # A real signature outside root: a row that points there must not be read.
    (tmp_path.parent / "escape.run.json").write_text('{"signature_schema": 2}', encoding="utf-8")
    reg = {
        "version": 1,
        "plans": {
            "a-missing": {"correlation_key": "a-missing", "signature_file": "nope.run.json"},
            "b-corrupt": {"correlation_key": "b-corrupt", "signature_file": "corrupt.run.json"},
            "c-not-dict-sig": {
                "correlation_key": "c-not-dict-sig",
                "signature_file": "list.run.json",
            },
            "d-not-dict-row": "malformed-entry",
            "e-escape": {"correlation_key": "e-escape", "signature_file": "../escape.run.json"},
        },
    }
    (tmp_path / "registry.json").write_text(json.dumps(reg), encoding="utf-8")
    rows, counts = scan(tmp_path)
    assert rows == []
    assert counts == {"scanned": 5, "signed": 0, "schema1": 0, "unsigned": 5}
    assert (
        format_result([], counts)
        == "scanned 5 rows, 0 with a signature, 0 schema-1, 5 without a signature, 0 matched"
    )
