"""PR comment helper — Task 8 (#9) commit 4.

Posts a triage report to an AIFactory PR via ``gh pr comment``. Used
by the Triager (commit 5) to surface verdicts on the PR that prompted
the test generation.

Design principles (same as git_writer):
  - **Dry-run first.** Default ``dry_run=True`` returns the argv
    without invoking subprocess. Safe for tests + CI.
  - **Body via stdin.** Multi-paragraph markdown is passed to
    ``gh pr comment --body-file -`` via stdin to avoid shell quoting
    issues. The argv only contains the flag; the body goes through
    the stdin channel of the injected runner_fn.
  - **Injected runner_fn.** Same shape as git_writer's.

Command shape:
  gh pr comment <pr_number> -R <owner>/<repo> --body-file -

The body is piped via stdin. The repo slug ``owner/repo`` is optional
— if omitted, ``gh`` infers from the cwd's git remote.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class PRCommentError(Exception):
    """Raised for fatal pr_comment failures."""


class _SubprocessResultLike(Protocol):
    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


# ─── Data shapes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PRCommentRequest:
    """One PR comment operation.

    Args:
        repo_dir: cwd for the gh invocation. ``gh`` infers the repo
            from the dir's git remote when ``repo_slug`` is None.
        pr_number: The PR number to comment on.
        body: The comment body (markdown). Passed via stdin so length
            + special characters are safe.
        repo_slug: Optional ``owner/repo`` override. If provided,
            passed via ``-R``. Otherwise ``gh`` uses the current
            repo's remote.
    """

    repo_dir: Path
    pr_number: int
    body: str = ""
    repo_slug: str | None = None


@dataclass(frozen=True)
class PRCommentResult:
    """Outcome of a post_pr_comment call.

    Args:
        ok: Whether the comment was posted (or dry-run completed).
        dry_run: Whether this was a dry-run.
        argv: The full argv sequence that was (or would have been)
            invoked.
        body_bytes: How many bytes of body were sent on stdin (real
            run) or would have been sent (dry-run).
        comment_url: URL returned by ``gh pr comment``'s stdout
            (real run only).
        error: Human-readable error if ok=False.
    """

    ok: bool = True
    dry_run: bool = True
    argv: tuple[str, ...] = field(default_factory=tuple)
    body_bytes: int = 0
    comment_url: str = ""
    error: str = ""


# ─── Subprocess seam ────────────────────────────────────────────────────


def _default_runner_fn(
    argv: list[str],
    *,
    cwd: Path,
    stdin: str | None = None,
) -> _SubprocessResultLike:
    """Default runner_fn that ACTUALLY shells out."""
    import subprocess

    return subprocess.run(
        argv,
        cwd=str(cwd),
        input=stdin if stdin is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


# ─── Argv assembly ──────────────────────────────────────────────────────


def _build_argv(request: PRCommentRequest) -> tuple[str, ...]:
    """Assemble the ``gh pr comment`` argv. Pure function — same
    request → same argv every time."""
    argv: list[str] = ["gh", "pr", "comment", str(request.pr_number)]
    if request.repo_slug:
        argv += ["-R", request.repo_slug]
    argv += ["--body-file", "-"]  # read from stdin
    return tuple(argv)


# ─── Public entrypoint ──────────────────────────────────────────────────


def post_pr_comment(
    request: PRCommentRequest,
    *,
    dry_run: bool = True,
    runner_fn: Callable[..., _SubprocessResultLike] | None = None,
) -> PRCommentResult:
    """Post the comment via ``gh pr comment``.

    Dry-run returns the argv + body_bytes without invoking subprocess.
    Real-run pipes the body via stdin and parses ``gh``'s stdout for
    the comment URL.

    Body validation:
      - Empty body → ok=False, no invocation.
      - PR number ≤ 0 → ok=False, no invocation.
    """
    runner = runner_fn or _default_runner_fn

    if request.pr_number <= 0:
        return PRCommentResult(
            ok=False,
            dry_run=dry_run,
            error=f"invalid PR number: {request.pr_number}",
        )
    if not request.body:
        return PRCommentResult(
            ok=False,
            dry_run=dry_run,
            error="empty body — refusing to post empty PR comment",
        )

    argv = _build_argv(request)
    body_bytes = len(request.body.encode("utf-8"))

    if dry_run:
        return PRCommentResult(
            ok=True,
            dry_run=True,
            argv=argv,
            body_bytes=body_bytes,
        )

    if not request.repo_dir.exists():
        return PRCommentResult(
            ok=False,
            dry_run=False,
            argv=argv,
            error=f"repo_dir does not exist: {request.repo_dir}",
        )

    res = runner(list(argv), cwd=request.repo_dir, stdin=request.body)
    if res.returncode != 0:
        return PRCommentResult(
            ok=False,
            dry_run=False,
            argv=argv,
            body_bytes=body_bytes,
            error=f"gh pr comment failed: {(res.stderr or '').strip()[:300]}",
        )

    # gh prints the comment URL on stdout — single line.
    url = (res.stdout or "").strip()
    return PRCommentResult(
        ok=True,
        dry_run=False,
        argv=argv,
        body_bytes=body_bytes,
        comment_url=url,
    )
