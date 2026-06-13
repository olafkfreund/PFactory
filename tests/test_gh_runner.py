"""Tests for the concrete gh runner + live PlanService.emit() wiring (#52)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.emit.gh_runner import (  # noqa: E402
    GhCliRunner,
    GhRunnerError,
    _parse_issue_number,
)
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund flow
Add a refund flow to the orders web app.
## Acceptance Criteria
- A finance user can issue a refund
- The order status becomes refunded
"""


class _FakeRun:
    """Records argv/stdin and returns canned subprocess results in sequence."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, argv, *, cwd, stdin=None):
        self.calls.append({"argv": argv, "cwd": cwd, "stdin": stdin})
        return self._results.pop(0)


def _ok(stdout="", stderr="", code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


# ── _parse_issue_number ──────────────────────────────────────────────────────


def test_parse_issue_number_from_url():
    assert _parse_issue_number("https://github.com/acme/widget/issues/142") == 142


def test_parse_issue_number_takes_last_line():
    assert _parse_issue_number("noise\nhttps://github.com/o/r/issues/7\n") == 7


def test_parse_issue_number_rejects_non_numeric():
    with pytest.raises(GhRunnerError):
        _parse_issue_number("not-a-url")


# ── create_issue ─────────────────────────────────────────────────────────────


def test_create_issue_builds_argv_and_parses_number():
    # create_issue first bootstraps each label (gh label create --force), then
    # creates the issue — so feed a result per label-create plus the create.
    run = _FakeRun([
        _ok(), _ok(),  # two label bootstraps
        _ok(stdout="https://github.com/acme/widget/issues/101\n"),
    ])
    gh = GhCliRunner("acme/widget", repo_dir=Path("/tmp"), runner_fn=run)

    number = gh.create_issue("My epic", "the body", ["pfactory", "type:epic"])
    assert number == 101

    create = next(c for c in run.calls if c["argv"][:3] == ["gh", "issue", "create"])
    argv = create["argv"]
    assert argv[:5] == ["gh", "issue", "create", "-R", "acme/widget"]
    assert "--title" in argv and "My epic" in argv
    assert argv.count("--label") == 2          # one per label
    assert create["stdin"] == "the body"        # body piped via stdin


def test_create_issue_bootstraps_missing_labels_first():
    # Each label is ensured via `gh label create --force` BEFORE the issue is
    # created, so a repo missing a taxonomy label no longer 500s the emit.
    run = _FakeRun([
        _ok(), _ok(),
        _ok(stdout="https://github.com/acme/widget/issues/7\n"),
    ])
    gh = GhCliRunner("acme/widget", repo_dir=Path("/tmp"), runner_fn=run)
    gh.create_issue("t", "b", ["priority:p2", "area:testing"])

    label_calls = [c for c in run.calls if c["argv"][:3] == ["gh", "label", "create"]]
    assert {c["argv"][3] for c in label_calls} == {"priority:p2", "area:testing"}
    assert all("--force" in c["argv"] for c in label_calls)
    # label bootstraps come before the issue create
    assert run.calls[-1]["argv"][:3] == ["gh", "issue", "create"]


def test_create_issue_raises_on_failure():
    run = _FakeRun([_ok(code=1, stderr="boom")])
    gh = GhCliRunner("acme/widget", runner_fn=run)
    with pytest.raises(GhRunnerError, match="gh issue create failed"):
        gh.create_issue("t", "b", [])


def test_empty_repo_slug_is_rejected():
    with pytest.raises(GhRunnerError):
        GhCliRunner("")


# ── link_sub_issue (best-effort, two gh api calls) ───────────────────────────


def test_link_sub_issue_resolves_id_then_posts():
    run = _FakeRun([_ok(stdout="99001\n"), _ok(stdout="{}")])
    gh = GhCliRunner("acme/widget", runner_fn=run)
    gh.link_sub_issue(10, 11)

    assert run.calls[0]["argv"][:2] == ["gh", "api"]           # resolve child id
    assert run.calls[1]["argv"][:3] == ["gh", "api", "--method"]  # POST sub_issue
    assert "sub_issue_id=99001" in run.calls[1]["argv"]


def test_link_sub_issue_raises_when_resolve_fails():
    run = _FakeRun([_ok(code=1, stderr="not found")])
    gh = GhCliRunner("acme/widget", runner_fn=run)
    with pytest.raises(GhRunnerError):
        gh.link_sub_issue(10, 11)


# ── PlanService.emit() end-to-end with a fake gh runner ──────────────────────


class _FakeGh:
    """A GhRunner that returns incrementing numbers and records links."""

    def __init__(self):
        self._n = 100
        self.links = []

    def create_issue(self, title, body, labels):
        self._n += 1
        return self._n

    def link_sub_issue(self, parent, child):
        self.links.append((parent, child))


def _processed(svc):
    s = svc.ingest_text(_PLAN, title="Refund flow")
    return svc.process(s.session_id)


def _approved(svc):
    """Process + human-approve so a live emit is governed (ready_to_emit)."""
    session = _processed(svc)
    assert session.review.gates_passed, "test plan must pass gates to be emittable"
    return svc.approve(session.session_id, approver="olaf")


def test_live_emit_reaches_emitted_with_real_correlation_ids():
    svc = PlanService()
    session = _approved(svc)
    gh = _FakeGh()

    out = svc.emit(session.session_id, repo="acme/widget", dry_run=False, gh=gh)

    assert out.status == "emitted"
    assert out.emitted_issue_number == 101          # the epic — first issue created
    assert out.correlation_key == "101"
    assert out.emit_result["epic_number"] == 101
    assert gh.links                                 # children linked to the epic


def test_dry_run_emit_needs_no_runner_and_stays_non_terminal():
    svc = PlanService()
    session = _processed(svc)
    out = svc.emit(session.session_id, repo="acme/widget", dry_run=True)
    assert out.status != "emitted"
    assert out.emit_result["dry_run"] is True


def test_link_warning_does_not_block_emitted():
    """A best-effort link failure is a warning, not a fatal error — emit still wins."""
    class _FlakyGh(_FakeGh):
        def link_sub_issue(self, parent, child):
            raise RuntimeError("sub-issue API unavailable")

    svc = PlanService()
    session = _approved(svc)
    out = svc.emit(session.session_id, repo="acme/widget", dry_run=False, gh=_FlakyGh())

    assert out.status == "emitted"                  # warnings don't block
    assert out.emit_result["warnings"]              # but they are recorded
    assert not out.emit_result["errors"]
