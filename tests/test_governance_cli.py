"""Governance query CLI tests (#1961 step 4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
    assert tuple(match.keys()) == tuple(sorted(MATCH_KEYS)) or set(match.keys()) == set(MATCH_KEYS)
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
        "controls": [
            "agentic-ai-governance",
            "audit-logging",
            "change-mgmt-sod",
            "data-governance",
            "runtime-isolation",
            "secrets-management",
        ],
        "agent_identity": None,
        "model_route": None,
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


def test_a_missing_or_corrupt_registry_fails_instead_of_reporting_zero(tmp_path, capsys):
    assert main(["--root", str(tmp_path)]) == 2
    (tmp_path / "registry.json").write_text("{not json", encoding="utf-8")
    assert main(["--root", str(tmp_path)]) == 2
    (tmp_path / "registry.json").write_text('{"plans": []}', encoding="utf-8")
    assert main(["--root", str(tmp_path)]) == 2
    assert "scanned" not in capsys.readouterr().out


def test_since_must_be_a_full_iso_day():
    with pytest.raises(SystemExit):
        main(["--root", str(FIXTURES), "--since", "2026-9-01"])


def test_control_characters_in_untrusted_fields_cannot_forge_a_line():
    forged = {k: None for k in MATCH_KEYS}
    forged["title"] = "x\nscanned 99 rows, 99 with a signature\x1b[2J"
    counts = {"scanned": 1, "signed": 1, "schema1": 0, "unsigned": 0}
    lines = format_result([forged], counts).splitlines()
    assert len(lines) == 2
    assert "\x1b" not in lines[0] and lines[1].startswith("scanned 1 rows")


def _root_with_identity(tmp_path: Path) -> Path:
    """The fixtures, with agent identity and route filled on the two schema-2 rows."""
    import shutil

    root = tmp_path / "docs"
    shutil.copytree(FIXTURES, root)
    for name, identity, route in (
        ("005-payments-refund-api", "agent:pfactory/k-1", "frontier"),
        ("006-docs-typo-fix", None, "cheap"),
    ):
        path = root / f"{name}.run.json"
        sig = json.loads(path.read_text())
        sig.update(agent_identity=identity, model_route=route)
        path.write_text(json.dumps(sig))
    return root


def test_json_projects_controls_agent_identity_and_model_route(tmp_path, capsys):
    root = _root_with_identity(tmp_path)
    assert main(["--root", str(root), "--tier", "hard", "--json"]) == 0
    (match,) = json.loads(capsys.readouterr().out)["matches"]
    assert match["agent_identity"] == "agent:pfactory/k-1"
    assert match["model_route"] == "frontier"
    assert "change-mgmt-sod" in match["controls"]


def test_text_line_prints_the_new_fields_after_the_old_ones(tmp_path, capsys):
    root = _root_with_identity(tmp_path)
    assert main(["--root", str(root), "--tier", "low"]) == 0
    line = capsys.readouterr().out.splitlines()[0]
    assert line.endswith("\tcontrols=audit-logging\tagent_identity=None\tmodel_route=cheap")
    assert line.index("created_at=") < line.index("controls=")


def test_control_and_tier_filters_are_exact_and_combine(tmp_path):
    rows, _ = scan(_root_with_identity(tmp_path))
    keys = lambda **kw: [m["correlation_key"] for m in select(rows, **kw)]  # noqa: E731
    assert keys(control="audit-logging") == ["pf-005-payments-refund-api", "pf-006-docs-typo-fix"]
    assert keys(control="change-mgmt-sod") == ["pf-005-payments-refund-api"]
    assert keys(control="change-mgmt-sod", tier="low") == []
    assert keys(control="audit-logging", tier="low") == ["pf-006-docs-typo-fix"]
    assert keys(control="no-such-control") == []  # and the schema-1 row never matches


def test_hostile_new_fields_cannot_forge_a_line():
    row = {k: None for k in MATCH_KEYS}
    row.update(controls=["a\nscanned 9 rows"], agent_identity="x\x1b[2J", model_route="y\r\nz")
    counts = {"scanned": 1, "signed": 1, "schema1": 0, "unsigned": 0}
    lines = format_result([row], counts).splitlines()
    assert len(lines) == 2 and "\x1b" not in lines[0]


# ── --repo: join a plan to the commits that carry its Plan-Slug trailer ────


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "commit", "-q", "--allow-empty", "-m", "no trailer")
    _git(r, "commit", "-q", "--allow-empty", "-m", "ship\n\nPlan-Slug: pf-005-payments-refund-api")
    _git(
        r, "commit", "-q", "--allow-empty", "-m", "other\n\nPlan-Slug: pf-005-payments-refund-api-x"
    )
    _git(r, "commit", "-q", "--allow-empty", "-m", "prose\n\nmentions pf-006-docs-typo-fix only")
    return r


def _head_of(repo: Path, grep: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%H", f"--grep={grep}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_repo_join_matches_the_trailer_value_exactly(repo, capsys):
    assert main(["--root", str(FIXTURES), "--repo", str(repo), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    by_key = {m["correlation_key"]: m["commits"] for m in payload["matches"]}
    assert by_key["pf-005-payments-refund-api"] == [_head_of(repo, "^ship")]  # not the -x slug
    assert by_key["pf-006-docs-typo-fix"] == []  # a mention in prose is not a trailer
    assert payload["counts"]["commits_with_plan_slug"] == 2


def test_repo_join_text_output_names_the_commits_and_the_count(repo, capsys):
    assert main(["--root", str(FIXTURES), "--repo", str(repo), "--control", "change-mgmt-sod"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith(f"\tcommits={_head_of(repo, '^ship')}")
    assert lines[-1] == "2 commits carry a Plan-Slug trailer"


def test_without_repo_the_output_is_unchanged(capsys):
    assert main(["--root", str(FIXTURES), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert all("commits" not in m for m in payload["matches"])
    assert "commits_with_plan_slug" not in payload["counts"]


def test_repo_that_cannot_be_read_is_an_error_not_an_empty_answer(tmp_path, capsys, monkeypatch):
    assert main(["--root", str(FIXTURES), "--repo", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err

    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("git", 60)

    monkeypatch.setattr(subprocess, "run", boom)
    assert main(["--root", str(FIXTURES), "--repo", str(tmp_path)]) == 2

    def missing(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    assert main(["--root", str(FIXTURES), "--repo", str(tmp_path)]) == 2


def test_a_repo_path_starting_with_a_dash_is_not_read_as_an_option(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "--version").mkdir()
    assert main(["--root", str(FIXTURES), "--repo=--version"]) == 2  # a plain error, no git output
    assert "error:" in capsys.readouterr().err
