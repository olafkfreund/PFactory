"""Tests for the living-template drift watcher (#27)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.templates.drift import (  # noqa: E402
    TemplateDriftReport,
    detect_drift,
    fetch_current_best_practices,
    propose_update,
    watch,
)
from plan.templates.models import Policy, Template, TemplateMetadata  # noqa: E402

# ── Fakes ──────────────────────────────────────────────────────────────


class FakeSource:
    """Injected best-practice provider — returns a fixed snapshot."""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    def snapshot(self, template_name: str) -> dict:
        self.calls.append(template_name)
        return self._snapshot


class FakeGit:
    """Records git/PR calls in order; never touches a real repo."""

    def __init__(self, pr_url: str = "https://example.test/pr/1") -> None:
        self.pr_url = pr_url
        self.calls: list[tuple] = []

    def create_branch(self, name: str) -> None:
        self.calls.append(("create_branch", name))

    def write_file(self, path: str, content: str) -> None:
        self.calls.append(("write_file", path, content))

    def commit(self, msg: str) -> None:
        self.calls.append(("commit", msg))

    def open_pr(self, title: str, body: str, base: str, head: str) -> str:
        self.calls.append(("open_pr", title, body, base, head))
        return self.pr_url


def _template() -> Template:
    return Template(
        metadata=TemplateMetadata(name="golden-gcp-service"),
        policy=Policy(
            allowed_regions=["europe-west1"],
            security_baselines=["cis-1.2"],
        ),
    )


# ── detect_drift ───────────────────────────────────────────────────────


def test_detect_drift_added_when_snapshot_newer() -> None:
    template = _template()
    current = {
        "allowed_regions": ["europe-west1", "europe-west9"],
        "security_baselines": ["cis-1.2", "cis-2.0"],
    }

    report = detect_drift(template, current)

    assert report.drifted is True
    assert report.added["allowed_regions"] == ["europe-west9"]
    assert report.added["security_baselines"] == ["cis-2.0"]
    # proposed_policy merges new items onto the existing ones.
    assert report.proposed_policy is not None
    assert report.proposed_policy["allowed_regions"] == [
        "europe-west1",
        "europe-west9",
    ]
    assert report.proposed_policy["security_baselines"] == ["cis-1.2", "cis-2.0"]
    assert "europe-west9" in report.summary


def test_detect_drift_identical_is_not_drifted() -> None:
    template = _template()
    current = {
        "allowed_regions": ["europe-west1"],
        "security_baselines": ["cis-1.2"],
    }

    report = detect_drift(template, current)

    assert report.drifted is False
    assert report.added == {}
    assert report.proposed_policy is None


def test_detect_drift_stale_items_are_notes_not_removed_by_default() -> None:
    template = _template()
    # Snapshot drops europe-west1 — conservative: surfaces as a changed note.
    current = {"allowed_regions": ["europe-west9"]}

    report = detect_drift(template, current)

    assert report.drifted is True
    assert report.added["allowed_regions"] == ["europe-west9"]
    assert report.changed["allowed_regions"] == ["europe-west1"]
    assert report.removed == {}
    # europe-west1 retained (not removed); europe-west9 appended.
    assert report.proposed_policy["allowed_regions"] == [
        "europe-west1",
        "europe-west9",
    ]


def test_detect_drift_allow_removals() -> None:
    template = _template()
    current = {"allowed_regions": ["europe-west9"]}

    report = detect_drift(template, current, allow_removals=True)

    assert report.removed["allowed_regions"] == ["europe-west1"]
    assert report.changed == {}
    assert report.proposed_policy["allowed_regions"] == ["europe-west9"]


# ── fetch_current_best_practices ───────────────────────────────────────


def test_fetch_none_source_returns_empty() -> None:
    assert fetch_current_best_practices(_template(), source=None) == {}


def test_fetch_calls_injected_source() -> None:
    source = FakeSource({"allowed_regions": ["europe-west9"]})

    snapshot = fetch_current_best_practices(_template(), source=source)

    assert snapshot == {"allowed_regions": ["europe-west9"]}
    assert source.calls == ["golden-gcp-service"]


def test_fetch_defensive_on_bad_source() -> None:
    class Bad:
        pass

    assert fetch_current_best_practices(_template(), source=Bad()) == {}


# ── propose_update ─────────────────────────────────────────────────────


def test_propose_update_no_drift() -> None:
    report = TemplateDriftReport(template="x", drifted=False)
    result = propose_update(
        report, repo="acme/templates", template_path="t.yaml"
    )
    assert result == {"opened": False, "reason": "no drift"}


def test_propose_update_dry_run_returns_proposal_without_calling_git() -> None:
    report = detect_drift(
        _template(), {"allowed_regions": ["europe-west1", "europe-west9"]}
    )
    git = FakeGit()

    result = propose_update(
        report,
        repo="acme/templates",
        template_path="templates/golden.yaml",
        git=git,
        dry_run=True,
    )

    assert result["opened"] is False
    assert result["dry_run"] is True
    # Proposal carries everything a reviewer needs.
    assert "title" in result and result["title"]
    assert "body" in result and "golden-gcp-service" in result["body"]
    assert "europe-west9" in result["body"]
    assert result["branch"] == "template-drift/golden-gcp-service"
    assert "europe-west9" in result["content"]
    # Crucially: git was NOT touched.
    assert git.calls == []


def test_propose_update_live_opens_pr_in_order() -> None:
    report = detect_drift(
        _template(), {"allowed_regions": ["europe-west1", "europe-west9"]}
    )
    git = FakeGit(pr_url="https://example.test/pr/42")

    result = propose_update(
        report,
        repo="acme/templates",
        template_path="templates/golden.yaml",
        git=git,
        dry_run=False,
    )

    assert result["opened"] is True
    assert result["pr_url"] == "https://example.test/pr/42"

    ops = [call[0] for call in git.calls]
    assert ops == ["create_branch", "write_file", "commit", "open_pr"]
    # Branch + path threaded through correctly.
    assert git.calls[0] == ("create_branch", "template-drift/golden-gcp-service")
    assert git.calls[1][1] == "templates/golden.yaml"
    # open_pr base is main.
    open_pr_call = git.calls[-1]
    assert open_pr_call[3] == "main"
    assert open_pr_call[4] == "template-drift/golden-gcp-service"


# ── watch (end-to-end) ─────────────────────────────────────────────────


def test_watch_opens_pr_with_newer_snapshot() -> None:
    source = FakeSource(
        {"allowed_regions": ["europe-west1", "europe-west9"]}
    )
    git = FakeGit(pr_url="https://example.test/pr/7")

    result = watch(
        _template(),
        source=source,
        repo="acme/templates",
        template_path="templates/golden.yaml",
        git=git,
        dry_run=False,
    )

    assert result["opened"] is True
    assert result["pr_url"] == "https://example.test/pr/7"
    assert [c[0] for c in git.calls] == [
        "create_branch",
        "write_file",
        "commit",
        "open_pr",
    ]
    assert result["report"]["drifted"] is True


def test_watch_none_source_no_drift_no_pr() -> None:
    git = FakeGit()

    result = watch(
        _template(),
        source=None,
        repo="acme/templates",
        template_path="templates/golden.yaml",
        git=git,
        dry_run=False,
    )

    assert result["opened"] is False
    assert result["reason"] == "no drift"
    assert git.calls == []
