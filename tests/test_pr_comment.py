"""Tests for the PR comment helper — Task 8 (#9) commit 4.

Default dry_run=True keeps the test suite safe — no actual gh calls.
Tests inject a recorder runner_fn to verify both argv construction
AND stdin-piping behaviour.

Covered:
  - Invalid pr_number (≤ 0) → ok=False, no invocation
  - Empty body → ok=False, refuses to post
  - Dry-run argv assembly (with + without repo_slug)
  - Body bytes counted in UTF-8
  - Real-run with recorder: success, gh failure (non-zero exit),
    missing repo_dir
  - Body passed via stdin, not argv
  - Comment URL captured from gh stdout
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from tools.pr_comment import (
    PRCommentRequest,
    PRCommentResult,
    post_pr_comment,
)

# ── Helpers ────────────────────────────────────────────────────────────


@dataclass
class _FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _RecordingRunner:
    def __init__(self, scripted: list[_FakeProc] | None = None):
        self.calls: list[tuple[list[str], Path, str | None]] = []
        self._scripted = list(scripted or [])

    def __call__(self, argv, *, cwd, stdin=None):
        self.calls.append((list(argv), cwd, stdin))
        if self._scripted:
            return self._scripted.pop(0)
        return _FakeProc(returncode=0)


# ── Validation ─────────────────────────────────────────────────────────


def test_invalid_pr_number_zero_rejected(tmp_path: Path) -> None:
    req = PRCommentRequest(repo_dir=tmp_path, pr_number=0, body="x")
    result = post_pr_comment(req, dry_run=True)
    assert result.ok is False
    assert "invalid PR number" in result.error


def test_invalid_pr_number_negative_rejected(tmp_path: Path) -> None:
    req = PRCommentRequest(repo_dir=tmp_path, pr_number=-1, body="x")
    result = post_pr_comment(req, dry_run=True)
    assert result.ok is False
    assert "invalid PR number" in result.error


def test_empty_body_rejected(tmp_path: Path) -> None:
    req = PRCommentRequest(repo_dir=tmp_path, pr_number=42, body="")
    result = post_pr_comment(req, dry_run=True)
    assert result.ok is False
    assert "empty body" in result.error


# ── Dry-run argv assembly ─────────────────────────────────────────────


def test_dry_run_argv_without_repo_slug(tmp_path: Path) -> None:
    """No repo_slug → gh infers from cwd; no -R flag."""
    req = PRCommentRequest(
        repo_dir=tmp_path / "repo",
        pr_number=42,
        body="hello",
    )
    result = post_pr_comment(req, dry_run=True)
    assert result.ok is True
    assert result.dry_run is True
    assert result.argv == ("gh", "pr", "comment", "42", "--body-file", "-")


def test_dry_run_argv_with_repo_slug(tmp_path: Path) -> None:
    req = PRCommentRequest(
        repo_dir=tmp_path / "repo",
        pr_number=42,
        body="hello",
        repo_slug="olafkfreund/PFactory",
    )
    result = post_pr_comment(req, dry_run=True)
    assert result.argv == (
        "gh", "pr", "comment", "42",
        "-R", "olafkfreund/PFactory",
        "--body-file", "-",
    )


def test_dry_run_body_bytes_count(tmp_path: Path) -> None:
    """body_bytes is UTF-8 byte length — not character count."""
    req = PRCommentRequest(
        repo_dir=tmp_path / "repo",
        pr_number=1,
        body="café",   # 5 bytes (4 ASCII + 2-byte é → wait, 1 byte more)
    )
    result = post_pr_comment(req, dry_run=True)
    # café = 99 97 102 195 169 = 5 bytes
    assert result.body_bytes == 5


def test_dry_run_skips_runner(tmp_path: Path) -> None:
    """Dry-run must not invoke runner_fn at all."""
    runner = _RecordingRunner()
    req = PRCommentRequest(
        repo_dir=tmp_path / "repo", pr_number=1, body="x",
    )
    post_pr_comment(req, dry_run=True, runner_fn=runner)
    assert runner.calls == []


def test_dry_run_no_comment_url(tmp_path: Path) -> None:
    """Dry-run never sets comment_url since gh wasn't called."""
    req = PRCommentRequest(
        repo_dir=tmp_path / "repo", pr_number=1, body="x",
    )
    result = post_pr_comment(req, dry_run=True)
    assert result.comment_url == ""


# ── Real-run with recorder ──────────────────────────────────────────────


def test_real_run_success_captures_url(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(
            returncode=0,
            stdout="https://github.com/olafkfreund/PFactory/pull/9#issuecomment-1234\n",
        ),
    ])
    req = PRCommentRequest(
        repo_dir=repo, pr_number=9,
        body="## Triage report\n\nLooks good.\n",
    )
    result = post_pr_comment(req, dry_run=False, runner_fn=runner)
    assert result.ok is True
    assert result.dry_run is False
    assert result.comment_url == (
        "https://github.com/olafkfreund/PFactory/pull/9#issuecomment-1234"
    )
    # body_bytes counted
    assert result.body_bytes > 0
    # Exactly one runner call
    assert len(runner.calls) == 1


def test_real_run_passes_body_via_stdin_not_argv(tmp_path: Path) -> None:
    """Multi-paragraph body with special characters lands on stdin —
    argv only contains --body-file -."""
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[_FakeProc(returncode=0)])
    body = (
        "## Triage report\n\n"
        "| Bucket | Count |\n"
        "|---|---:|\n"
        "| Committed | 3 |\n\n"
        "## Reasons\n"
        "- `assert True` survived mutation\n"
    )
    req = PRCommentRequest(repo_dir=repo, pr_number=9, body=body)
    post_pr_comment(req, dry_run=False, runner_fn=runner)

    argv_recorded, cwd_recorded, stdin_recorded = runner.calls[0]
    # Body NOT in argv
    for chunk in argv_recorded:
        assert "Triage report" not in chunk
    # Body IS in stdin
    assert stdin_recorded == body
    # cwd is the repo_dir
    assert cwd_recorded == repo


def test_real_run_missing_repo_dir(tmp_path: Path) -> None:
    runner = _RecordingRunner()
    req = PRCommentRequest(
        repo_dir=tmp_path / "missing", pr_number=1, body="x",
    )
    result = post_pr_comment(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "does not exist" in result.error
    # No runner invocation
    assert runner.calls == []


def test_real_run_gh_failure_captures_stderr(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(
            returncode=1,
            stderr="error: unauthorized; run gh auth login\n",
        ),
    ])
    req = PRCommentRequest(repo_dir=repo, pr_number=9, body="x")
    result = post_pr_comment(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "gh pr comment failed" in result.error
    assert "unauthorized" in result.error


def test_real_run_gh_failure_preserves_argv(tmp_path: Path) -> None:
    """Failures still surface the argv that was attempted — for the
    Triager's status.json debug field."""
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[_FakeProc(returncode=1, stderr="x")])
    req = PRCommentRequest(repo_dir=repo, pr_number=42, body="b")
    result = post_pr_comment(req, dry_run=False, runner_fn=runner)
    assert result.argv == (
        "gh", "pr", "comment", "42", "--body-file", "-",
    )


def test_long_body_via_stdin_unaffected(tmp_path: Path) -> None:
    """A 20KB body lands on stdin without truncation or shell issues."""
    repo = tmp_path / "repo"
    repo.mkdir()
    big_body = "x" * 20_000 + "\nTAIL"
    runner = _RecordingRunner(scripted=[_FakeProc(returncode=0, stdout="url")])
    req = PRCommentRequest(repo_dir=repo, pr_number=1, body=big_body)
    result = post_pr_comment(req, dry_run=False, runner_fn=runner)
    assert result.ok is True
    _, _, stdin_recorded = runner.calls[0]
    assert stdin_recorded == big_body
    assert stdin_recorded.endswith("TAIL")
    # body_bytes matches the full length
    assert result.body_bytes == len(big_body.encode("utf-8"))


def test_pr_number_in_argv_is_string(tmp_path: Path) -> None:
    """gh wants the PR number as a string positional. Make sure we
    don't accidentally pass an int (argv must be all str)."""
    req = PRCommentRequest(
        repo_dir=tmp_path / "repo", pr_number=42, body="x",
    )
    result = post_pr_comment(req, dry_run=True)
    for chunk in result.argv:
        assert isinstance(chunk, str)
