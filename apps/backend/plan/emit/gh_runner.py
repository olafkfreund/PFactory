"""Concrete ``gh`` runner for live plan emit (#52).

Implements the :class:`~plan.emit.github_emitter.GhRunner` contract by shelling
out to the GitHub CLI — the same dry-run-first, injected-``runner_fn`` pattern as
:mod:`tools.pr_comment`. Used by :meth:`plan.service.PlanService.emit` when a
human has approved a live emit; side-effects stay opt-in (dry-run by default) per
the no-automatic-pushes policy.

Command shapes:
  gh issue create -R <owner/repo> --title <t> --body-file - [--label <l> ...]
  gh api repos/<owner/repo>/issues/<child> --jq .id            (resolve db id)
  gh api --method POST repos/<owner/repo>/issues/<parent>/sub_issues -F sub_issue_id=<id>

Issue bodies are piped via stdin (``--body-file -``) to avoid shell-quoting
issues. ``gh issue create`` prints the new issue's URL on stdout; the trailing
path segment is the issue number.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol


class GhRunnerError(RuntimeError):
    """Raised when a ``gh`` invocation fails."""


class _SubprocessResultLike(Protocol):
    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


def _default_runner_fn(
    argv: list[str], *, cwd: Path, stdin: str | None = None,
) -> _SubprocessResultLike:
    """Default runner that ACTUALLY shells out."""
    import subprocess

    return subprocess.run(
        argv, cwd=str(cwd),
        input=stdin if stdin is not None else None,
        capture_output=True, text=True, check=False,
    )


def _parse_issue_number(stdout: str) -> int:
    """Extract the issue number from ``gh issue create`` stdout (an issue URL)."""
    url = (stdout or "").strip().splitlines()[-1].strip() if stdout.strip() else ""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if not tail.isdigit():
        raise GhRunnerError(f"could not parse issue number from gh output: {url!r}")
    return int(tail)


class GhCliRunner:
    """A :class:`GhRunner` that creates issues via the ``gh`` CLI.

    Args:
        repo_slug: ``owner/repo`` the issues are created in (passed via ``-R``).
        repo_dir: cwd for the ``gh`` invocations (default: current directory).
        runner_fn: injected subprocess seam (defaults to a real ``subprocess.run``);
            tests pass a fake that records argv and returns canned results.
    """

    def __init__(
        self,
        repo_slug: str,
        *,
        repo_dir: Path | None = None,
        runner_fn: Callable[..., _SubprocessResultLike] | None = None,
    ) -> None:
        if not repo_slug:
            raise GhRunnerError("repo_slug ('owner/repo') is required for live emit")
        self._repo = repo_slug
        self._cwd = repo_dir or Path.cwd()
        self._run = runner_fn or _default_runner_fn

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        argv = ["gh", "issue", "create", "-R", self._repo,
                "--title", title, "--body-file", "-"]
        for label in labels:
            argv += ["--label", label]
        res = self._run(argv, cwd=self._cwd, stdin=body)
        if res.returncode != 0:
            raise GhRunnerError(
                f"gh issue create failed: {(res.stderr or '').strip()[:300]}"
            )
        return _parse_issue_number(res.stdout)

    def link_sub_issue(self, parent: int, child: int) -> None:
        """Link ``child`` under ``parent`` via the GitHub sub-issues API.

        Best-effort: the sub-issues endpoint needs the child's *database id*, so
        this resolves it first. Any failure raises ``GhRunnerError`` — the emitter
        records it as a (non-fatal) warning and the emit still succeeds.
        """
        res = self._run(
            ["gh", "api", f"repos/{self._repo}/issues/{child}", "--jq", ".id"],
            cwd=self._cwd, stdin=None,
        )
        if res.returncode != 0:
            raise GhRunnerError(
                f"resolve issue #{child} id failed: {(res.stderr or '').strip()[:200]}"
            )
        child_id = (res.stdout or "").strip()
        res2 = self._run(
            ["gh", "api", "--method", "POST",
             f"repos/{self._repo}/issues/{parent}/sub_issues",
             "-F", f"sub_issue_id={child_id}"],
            cwd=self._cwd, stdin=None,
        )
        if res2.returncode != 0:
            raise GhRunnerError(
                f"link sub-issue #{child}→#{parent} failed: "
                f"{(res2.stderr or '').strip()[:200]}"
            )
