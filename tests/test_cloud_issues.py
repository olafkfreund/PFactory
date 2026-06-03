"""Tests for cloud findings → GitHub issues (#133/#152)."""

from __future__ import annotations

from agents.cloud.assessment import parse_ocsf
from agents.cloud.issues import (
    IssueSpec,
    build_issue_specs,
    issue_specs_to_dict,
    register_issues,
)


def _rec(severity="High", title="MFA disabled", check="iam_mfa",
         desc="Enable MFA.", risk="Takeover.", refs=("https://x",)):
    return {
        "status_code": "FAIL",
        "severity": severity,
        "finding_info": {"title": title, "uid": f"prowler-{check}-1"},
        "metadata": {"event_code": check},
        "resources": [{"name": "user-a", "region": "us-east-1"}],
        "cloud": {"region": "us-east-1"},
        "remediation": {"desc": desc, "references": list(refs)},
        "risk_details": risk,
    }


def test_build_epic_and_children() -> None:
    epic, children = build_issue_specs(
        parse_ocsf([_rec(severity="High"), _rec(severity="Critical", title="root", check="root")]),
        provider="aws", account="123",
    )
    assert "Remediation" in epic.title and "123" in epic.title
    assert "epic" in epic.labels and "cloud" in epic.labels
    assert len(children) == 2
    # body carries what's wrong + how to fix
    body = children[0].body
    assert "## What's wrong" in body and "## How to fix" in body and "References" in body
    # severity label
    assert any(lbl.startswith("severity:") for lbl in children[0].labels)


def test_children_ordered_critical_first() -> None:
    _epic, children = build_issue_specs(
        parse_ocsf([_rec(severity="Medium", title="ZZmed", check="m"),
                    _rec(severity="Critical", title="ZZcrit", check="c")]),
        provider="aws", account="1",
    )
    assert "ZZcrit" in children[0].title


def test_issue_specs_to_dict_roundtrips() -> None:
    epic, children = build_issue_specs(parse_ocsf([_rec()]), provider="aws", account="1")
    d = issue_specs_to_dict(epic, children)
    assert d["epic"]["title"] == epic.title
    assert d["children"][0]["title"] == children[0].title
    assert "labels" in d["children"][0]


# ── register (dry-run + create with injected gh) ─────────────────────────────


def test_register_dry_run_makes_no_calls() -> None:
    epic, children = build_issue_specs(parse_ocsf([_rec()]), provider="aws", account="1")
    calls = []
    result = register_issues(epic, children, "o/r", create=False, gh_runner=lambda a: calls.append(a))
    assert result["dry_run"] is True
    assert result["count"] == 1
    assert calls == []  # nothing created


def test_register_create_calls_gh_and_links_epic() -> None:
    epic, children = build_issue_specs(parse_ocsf([_rec()]), provider="aws", account="1")
    created = []

    def fake_gh(argv):
        created.append(argv)
        # gh issue create prints the new issue URL
        n = len(created)
        return (0, f"https://github.com/o/r/issues/{n}")

    result = register_issues(epic, children, "o/r", create=True, gh_runner=fake_gh)
    assert result["dry_run"] is False
    assert result["epic"].endswith("/issues/1")
    assert len(result["children"]) == 1
    # child references the epic number
    child_argv = created[1]
    body = child_argv[child_argv.index("--body") + 1]
    assert "Part of epic #1." in body


def test_issuespec_to_dict() -> None:
    s = IssueSpec("t", "b", ["x"])
    assert s.to_dict() == {"title": "t", "body": "b", "labels": ["x"]}
