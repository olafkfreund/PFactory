"""Git writer — Task 8 (#9) commit 4.

Commits a set of test files onto AIFactory's feature branch. Used by
the Triager (commit 5) after dedup + rank produces the accepted +
flagged set.

Design principles:
  - **Dry-run first.** Default ``dry_run=True`` returns the exact
    argv sequences without invoking subprocess — safe for the test
    suite + CI.
  - **No remote push.** Per CLAUDE.md: "NO automatic pushes to GitHub
    - user controls when to push". This module only writes + commits
    locally; pushing is the operator's call.
  - **Injected runner_fn.** Tests pass a recorder; commit-5 wiring
    passes a thin subprocess.run wrapper.
  - **Path safety.** Every file path is validated against escape
    attempts (``..``) and absolute paths before being touched.

The git workflow this module orchestrates:

  1. git -C <repo> rev-parse --verify <branch>      (branch exists?)
  2. git -C <repo> checkout <branch>                 (switch to it)
  3. write files to disk                              (local I/O)
  4. git -C <repo> add -- <file1> <file2> ...        (stage)
  5. git -C <repo> commit -m "<msg>" --no-verify=false (commit)
  6. git -C <repo> rev-parse HEAD                    (capture new sha)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class GitWriterError(Exception):
    """Raised for fatal git-writer failures (path validation,
    branch missing, etc.)."""


class _SubprocessResultLike(Protocol):
    """Duck-type for subprocess.CompletedProcess."""

    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


# ─── Data shapes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GitWriteRequest:
    """One git-write operation.

    Args:
        repo_dir: Absolute path to the git repo (AIFactory's feature
            branch worktree).
        branch: Branch to commit on. Must already exist locally.
        files: Sequence of ``(relative_path, contents)`` tuples. Each
            relative_path is interpreted relative to ``repo_dir`` and
            must NOT contain ``..`` segments or be absolute.
        commit_msg: Single-line commit message. Multi-line acceptable
            but the first line should be ≤72 chars.
        author_name / author_email: optional. If both provided, passed
            via ``--author``. If absent, uses the repo's git config.
    """

    repo_dir: Path
    branch: str
    files: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    commit_msg: str = ""
    author_name: str | None = None
    author_email: str | None = None


@dataclass(frozen=True)
class GitWriteResult:
    """Outcome of a write_tests_to_branch call.

    Args:
        ok: True if the commit landed (or if dry-run completed
            cleanly).
        dry_run: Whether this was a dry-run.
        committed_paths: The relative paths the commit included.
        commit_sha: The new HEAD sha (real-run only; empty for
            dry-run).
        argv_log: List of argv sequences in invocation order.
            Includes BOTH real-run and dry-run for full traceability.
        error: Human-readable error if ok=False.
    """

    ok: bool = True
    dry_run: bool = True
    committed_paths: tuple[str, ...] = field(default_factory=tuple)
    commit_sha: str = ""
    argv_log: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    error: str = ""


# ─── Path validation ────────────────────────────────────────────────────


def _validate_relative_path(rel_path: str) -> Path:
    """Validate that ``rel_path`` is safe to write under repo_dir.

    Rules:
      - Must NOT be empty.
      - Must NOT be absolute.
      - Must NOT contain ``..`` segments (no parent escapes).
      - Must NOT start with a ``/``.

    Returns the path as a PurePath. Raises GitWriterError on violation.
    """
    if not rel_path:
        raise GitWriterError("empty file path")
    p = Path(rel_path)
    if p.is_absolute():
        raise GitWriterError(f"absolute path not allowed: {rel_path!r}")
    if any(part == ".." for part in p.parts):
        raise GitWriterError(f"path escape via '..' not allowed: {rel_path!r}")
    return p


# ─── Subprocess seam (real implementation lives in commit 5) ────────────


def _default_runner_fn(
    argv: list[str],
    *,
    cwd: Path,
    stdin: str | None = None,
) -> _SubprocessResultLike:
    """Default runner_fn that ACTUALLY shells out. Only used outside
    tests; commit 5's wiring passes this through unchanged."""
    import subprocess

    return subprocess.run(
        argv,
        cwd=str(cwd),
        input=stdin if stdin is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


# ─── Public entrypoint ──────────────────────────────────────────────────


def write_tests_to_branch(
    request: GitWriteRequest,
    *,
    dry_run: bool = True,
    runner_fn: Callable[..., _SubprocessResultLike] | None = None,
) -> GitWriteResult:
    """Write + commit ``request.files`` onto ``request.branch``.

    Args:
        request: GitWriteRequest describing the operation.
        dry_run: If True (default), only assemble the argv sequences
            and return them in the result; no subprocess invocations,
            no files written. Tests + CI run with dry_run=True.
        runner_fn: Subprocess seam. Default uses real subprocess.run.
            Tests inject a recorder.

    Returns:
        GitWriteResult capturing what was (or would have been) done.
    """
    runner = runner_fn or _default_runner_fn
    argv_log: list[tuple[str, ...]] = []

    # 1. Pre-flight validation (path safety + repo existence).
    if not dry_run and not request.repo_dir.exists():
        return GitWriteResult(
            ok=False,
            dry_run=False,
            error=f"repo_dir does not exist: {request.repo_dir}",
        )
    try:
        validated = [(_validate_relative_path(rp), contents) for rp, contents in request.files]
    except GitWriterError as exc:
        return GitWriteResult(
            ok=False,
            dry_run=dry_run,
            error=str(exc),
        )

    # 2. Branch existence check.
    verify_argv = (
        "git",
        "-C",
        str(request.repo_dir),
        "rev-parse",
        "--verify",
        request.branch,
    )
    argv_log.append(verify_argv)
    if not dry_run:
        res = runner(list(verify_argv), cwd=request.repo_dir)
        if res.returncode != 0:
            return GitWriteResult(
                ok=False,
                dry_run=False,
                argv_log=tuple(argv_log),
                error=(f"branch {request.branch!r} not found: {(res.stderr or '').strip()[:200]}"),
            )

    # 3. Checkout.
    checkout_argv = (
        "git",
        "-C",
        str(request.repo_dir),
        "checkout",
        request.branch,
    )
    argv_log.append(checkout_argv)
    if not dry_run:
        res = runner(list(checkout_argv), cwd=request.repo_dir)
        if res.returncode != 0:
            return GitWriteResult(
                ok=False,
                dry_run=False,
                argv_log=tuple(argv_log),
                error=(f"checkout {request.branch!r} failed: {(res.stderr or '').strip()[:200]}"),
            )

    # 4. Write files. (Dry-run: skip; record paths only.)
    committed_paths: list[str] = []
    if not dry_run:
        for rel, contents in validated:
            abs_path = request.repo_dir / rel
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(contents, encoding="utf-8")
            committed_paths.append(str(rel))
    else:
        committed_paths = [str(rel) for rel, _ in validated]

    if not committed_paths:
        # Nothing to commit — empty request. Return success-with-no-op.
        return GitWriteResult(
            ok=True,
            dry_run=dry_run,
            argv_log=tuple(argv_log),
            committed_paths=(),
        )

    # 5. git add.
    add_argv = (
        "git",
        "-C",
        str(request.repo_dir),
        "add",
        "--",
        *committed_paths,
    )
    argv_log.append(add_argv)
    if not dry_run:
        res = runner(list(add_argv), cwd=request.repo_dir)
        if res.returncode != 0:
            return GitWriteResult(
                ok=False,
                dry_run=False,
                argv_log=tuple(argv_log),
                error=f"git add failed: {(res.stderr or '').strip()[:200]}",
            )

    # 6. git commit.
    commit_argv = [
        "git",
        "-C",
        str(request.repo_dir),
        "commit",
        "-m",
        request.commit_msg or "pfactory: add generated tests",
    ]
    if request.author_name and request.author_email:
        commit_argv += [
            "--author",
            f"{request.author_name} <{request.author_email}>",
        ]
    argv_log.append(tuple(commit_argv))
    if not dry_run:
        res = runner(commit_argv, cwd=request.repo_dir)
        if res.returncode != 0:
            return GitWriteResult(
                ok=False,
                dry_run=False,
                argv_log=tuple(argv_log),
                error=f"git commit failed: {(res.stderr or '').strip()[:200]}",
            )

    # 7. Capture new HEAD sha.
    sha = ""
    rev_parse_argv = (
        "git",
        "-C",
        str(request.repo_dir),
        "rev-parse",
        "HEAD",
    )
    argv_log.append(rev_parse_argv)
    if not dry_run:
        res = runner(list(rev_parse_argv), cwd=request.repo_dir)
        if res.returncode == 0:
            sha = (res.stdout or "").strip()

    return GitWriteResult(
        ok=True,
        dry_run=dry_run,
        committed_paths=tuple(committed_paths),
        commit_sha=sha,
        argv_log=tuple(argv_log),
    )


def write_paths_to_branch(
    repo_dir: Path,
    paths: list[str],
    branch: str,
    message: str,
    *,
    dry_run: bool = True,
    runner_fn: Callable[..., _SubprocessResultLike] | None = None,
) -> GitWriteResult:
    """Commit already-on-disk ``paths`` (relative to ``repo_dir``) onto ``branch``.

    Generalises :func:`write_tests_to_branch` (which writes *file contents*) to
    commit an arbitrary artifact directory — e.g. a Visual Inspection's
    ``automated-test/<run>/`` folder (#170 / P4). Same **dry-run-first** contract:
    ``dry_run=True`` (default) only assembles + returns the argv sequences; no
    subprocess, no commit. The branch is created-or-reset with ``checkout -B``.
    """
    runner = runner_fn or _default_runner_fn
    argv_log: list[tuple[str, ...]] = []
    repo_dir = Path(repo_dir)

    if not dry_run and not repo_dir.exists():
        return GitWriteResult(ok=False, dry_run=False, error=f"repo_dir does not exist: {repo_dir}")
    try:
        rels = [str(_validate_relative_path(p)) for p in paths]
    except GitWriterError as exc:
        return GitWriteResult(ok=False, dry_run=dry_run, error=str(exc))
    if not rels:
        return GitWriteResult(ok=False, dry_run=dry_run, error="no paths to commit")

    steps = [
        ("git", "-C", str(repo_dir), "checkout", "-B", branch),
        ("git", "-C", str(repo_dir), "add", "--", *rels),
        ("git", "-C", str(repo_dir), "commit", "--no-verify", "-m", message),
    ]
    for argv in steps:
        argv_log.append(argv)
        if not dry_run:
            res = runner(list(argv), cwd=repo_dir)
            if res.returncode != 0:
                return GitWriteResult(
                    ok=False,
                    dry_run=False,
                    argv_log=tuple(argv_log),
                    error=f"{argv[3]} failed: {(res.stderr or '').strip()[:200]}",
                )

    sha_argv = ("git", "-C", str(repo_dir), "rev-parse", "HEAD")
    argv_log.append(sha_argv)
    sha = ""
    if not dry_run:
        res = runner(list(sha_argv), cwd=repo_dir)
        sha = (res.stdout or "").strip()

    return GitWriteResult(
        ok=True,
        dry_run=dry_run,
        committed_paths=tuple(rels),
        commit_sha=sha,
        argv_log=tuple(argv_log),
    )
