"""Tests for the git writer — Task 8 (#9) commit 4.

Default mode is ``dry_run=True`` so no test ever shells out to real
git. Tests inject a recorder ``runner_fn`` to verify both the argv
construction AND the orchestration when the writer thinks it's
calling subprocess for real.

Covered:
  - Path validation: empty, absolute, '..' escapes → GitWriterError
  - Dry-run argv assembly: all 5 stages produce the expected argv
  - Empty file list → no-op result, no add/commit argv emitted
  - Real-run (with recorder): success path, branch verify failure,
    checkout failure, add failure, commit failure
  - Author override appends --author flag
  - Captured commit_sha on success
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from tools.git_writer import (
    GitWriteRequest,
    GitWriterError,
    GitWriteResult,
    _validate_relative_path,
    write_tests_to_branch,
)

# ── Helpers ────────────────────────────────────────────────────────────


@dataclass
class _FakeProc:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _RecordingRunner:
    """Records every runner_fn call; returns scripted results."""

    def __init__(self, scripted: list[_FakeProc] | None = None):
        self.calls: list[tuple[list[str], Path, str | None]] = []
        self._scripted = list(scripted or [])

    def __call__(self, argv, *, cwd, stdin=None):
        self.calls.append((list(argv), cwd, stdin))
        if self._scripted:
            return self._scripted.pop(0)
        return _FakeProc(returncode=0)


# ── Path validation ────────────────────────────────────────────────────


def test_validate_empty_path_rejected() -> None:
    with pytest.raises(GitWriterError, match="empty"):
        _validate_relative_path("")


def test_validate_absolute_path_rejected() -> None:
    with pytest.raises(GitWriterError, match="absolute"):
        _validate_relative_path("/etc/passwd")


def test_validate_parent_escape_rejected() -> None:
    with pytest.raises(GitWriterError, match=r"\.\."):
        _validate_relative_path("../etc/passwd")


def test_validate_nested_parent_escape_rejected() -> None:
    with pytest.raises(GitWriterError, match=r"\.\."):
        _validate_relative_path("tests/../../../etc/passwd")


def test_validate_legitimate_path_accepted() -> None:
    p = _validate_relative_path("tests/test_x.py")
    assert str(p) == "tests/test_x.py"


# ── Dry-run argv assembly ─────────────────────────────────────────────


def test_dry_run_produces_all_argvs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    req = GitWriteRequest(
        repo_dir=repo,
        branch="auto-claude/feat",
        files=(
            ("tests/test_a.py", "def test_a(): pass\n"),
            ("tests/test_b.py", "def test_b(): pass\n"),
        ),
        commit_msg="pfactory: add 2 generated tests",
    )
    result = write_tests_to_branch(req, dry_run=True)
    assert isinstance(result, GitWriteResult)
    assert result.ok is True
    assert result.dry_run is True
    assert result.commit_sha == ""  # no real run

    argvs = result.argv_log
    # 5 stages: verify, checkout, add, commit, rev-parse HEAD
    assert len(argvs) == 5
    assert argvs[0] == (
        "git", "-C", str(repo), "rev-parse", "--verify", "auto-claude/feat",
    )
    assert argvs[1] == ("git", "-C", str(repo), "checkout", "auto-claude/feat")
    assert argvs[2] == (
        "git", "-C", str(repo), "add", "--",
        "tests/test_a.py", "tests/test_b.py",
    )
    assert argvs[3] == (
        "git", "-C", str(repo), "commit", "-m",
        "pfactory: add 2 generated tests",
    )
    assert argvs[4] == ("git", "-C", str(repo), "rev-parse", "HEAD")


def test_dry_run_records_committed_paths(tmp_path: Path) -> None:
    """Even in dry-run we report the paths that WOULD have been
    committed — so the Triager can include them in its report."""
    req = GitWriteRequest(
        repo_dir=tmp_path / "repo",
        branch="b",
        files=(("tests/x.py", "x"), ("tests/y.py", "y")),
    )
    result = write_tests_to_branch(req, dry_run=True)
    assert result.committed_paths == ("tests/x.py", "tests/y.py")


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    """Dry run must NOT touch disk."""
    repo = tmp_path / "repo"
    repo.mkdir()
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("tests/x.py", "should not exist"),),
    )
    write_tests_to_branch(req, dry_run=True)
    assert not (repo / "tests" / "x.py").exists()


def test_empty_file_list_no_add_commit() -> None:
    """Empty file list → only verify + checkout, no add/commit/rev-parse."""
    req = GitWriteRequest(
        repo_dir=Path("/r"), branch="b", files=(), commit_msg="x",
    )
    result = write_tests_to_branch(req, dry_run=True)
    assert result.ok is True
    assert result.committed_paths == ()
    # Only verify + checkout argvs emitted
    assert len(result.argv_log) == 2


def test_dry_run_validates_paths_eagerly(tmp_path: Path) -> None:
    """Path validation runs even in dry-run — catches issues early."""
    req = GitWriteRequest(
        repo_dir=tmp_path, branch="b",
        files=(("../bad.py", "x"),),
    )
    result = write_tests_to_branch(req, dry_run=True)
    assert result.ok is False
    assert ".." in result.error


def test_default_commit_message_used(tmp_path: Path) -> None:
    """Empty commit_msg → sensible default."""
    req = GitWriteRequest(
        repo_dir=tmp_path / "repo",
        branch="b",
        files=(("tests/x.py", "x"),),
        commit_msg="",
    )
    result = write_tests_to_branch(req, dry_run=True)
    commit_argv = result.argv_log[3]
    assert "-m" in commit_argv
    msg_idx = commit_argv.index("-m") + 1
    assert "pfactory" in commit_argv[msg_idx]


def test_author_override_appends_author_flag(tmp_path: Path) -> None:
    req = GitWriteRequest(
        repo_dir=tmp_path / "repo", branch="b",
        files=(("tests/x.py", "x"),),
        commit_msg="msg",
        author_name="PFactory Bot",
        author_email="bot@pfactory.local",
    )
    result = write_tests_to_branch(req, dry_run=True)
    commit_argv = result.argv_log[3]
    assert "--author" in commit_argv
    author_idx = commit_argv.index("--author") + 1
    assert commit_argv[author_idx] == "PFactory Bot <bot@pfactory.local>"


def test_author_partial_does_not_add_flag(tmp_path: Path) -> None:
    """Only one of name/email → don't emit a half-formed --author."""
    req = GitWriteRequest(
        repo_dir=tmp_path / "repo", branch="b",
        files=(("tests/x.py", "x"),),
        commit_msg="msg",
        author_name="PFactory Bot",   # no email
    )
    result = write_tests_to_branch(req, dry_run=True)
    commit_argv = result.argv_log[3]
    assert "--author" not in commit_argv


# ── Real-run with recorder ──────────────────────────────────────────────


def test_real_run_success_writes_files_and_captures_sha(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(returncode=0),  # verify
        _FakeProc(returncode=0),  # checkout
        _FakeProc(returncode=0),  # add
        _FakeProc(returncode=0),  # commit
        _FakeProc(returncode=0, stdout="abc123def\n"),  # rev-parse HEAD
    ])
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("tests/x.py", "def test_x(): pass\n"),),
        commit_msg="msg",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is True
    assert result.dry_run is False
    assert result.commit_sha == "abc123def"
    assert result.committed_paths == ("tests/x.py",)
    # File actually written
    assert (repo / "tests" / "x.py").read_text() == "def test_x(): pass\n"
    # All 5 stages invoked
    assert len(runner.calls) == 5


def test_real_run_missing_repo(tmp_path: Path) -> None:
    runner = _RecordingRunner()
    req = GitWriteRequest(
        repo_dir=tmp_path / "missing",  # doesn't exist
        branch="b", files=(("tests/x.py", "x"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "does not exist" in result.error
    # No runner calls — we bailed pre-flight
    assert runner.calls == []


def test_real_run_branch_verify_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(returncode=1, stderr="fatal: unknown revision"),
    ])
    req = GitWriteRequest(
        repo_dir=repo, branch="nope",
        files=(("tests/x.py", "x"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "branch 'nope' not found" in result.error
    # Only the verify call was made (checkout skipped)
    assert len(runner.calls) == 1


def test_real_run_checkout_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(returncode=0),  # verify OK
        _FakeProc(returncode=1, stderr="error: pathspec did not match"),
    ])
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("tests/x.py", "x"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "checkout 'b' failed" in result.error
    assert len(runner.calls) == 2  # verify + checkout
    # No files were written (we bailed at checkout)
    assert not (repo / "tests" / "x.py").exists()


def test_real_run_add_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(returncode=0),  # verify
        _FakeProc(returncode=0),  # checkout
        _FakeProc(returncode=1, stderr="fatal: pathspec"),
    ])
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("tests/x.py", "x"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "git add failed" in result.error


def test_real_run_commit_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(returncode=0),  # verify
        _FakeProc(returncode=0),  # checkout
        _FakeProc(returncode=0),  # add
        _FakeProc(returncode=1, stderr="nothing to commit"),
    ])
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("tests/x.py", "x"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is False
    assert "git commit failed" in result.error


def test_real_run_rev_parse_failure_does_not_fail_overall(
    tmp_path: Path,
) -> None:
    """If rev-parse HEAD fails (unlikely after successful commit) we
    don't fail the result — just leave commit_sha empty."""
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[
        _FakeProc(returncode=0),  # verify
        _FakeProc(returncode=0),  # checkout
        _FakeProc(returncode=0),  # add
        _FakeProc(returncode=0),  # commit
        _FakeProc(returncode=1, stderr="git in a weird state"),  # rev-parse
    ])
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("tests/x.py", "x"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is True
    assert result.commit_sha == ""


def test_real_run_creates_nested_directories(tmp_path: Path) -> None:
    """File paths with multiple directory levels work."""
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _RecordingRunner(scripted=[_FakeProc(returncode=0)] * 5)
    req = GitWriteRequest(
        repo_dir=repo, branch="b",
        files=(("a/b/c/test_deep.py", "deep"),), commit_msg="m",
    )
    result = write_tests_to_branch(req, dry_run=False, runner_fn=runner)
    assert result.ok is True
    assert (repo / "a" / "b" / "c" / "test_deep.py").read_text() == "deep"
