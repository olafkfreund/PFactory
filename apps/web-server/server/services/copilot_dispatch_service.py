"""Copilot cloud agent dispatch (epic #87 / #88, Component 2).

When a PFactory planning issue is labeled ``copilot:delegate``, PFactory assigns
it to GitHub's Copilot cloud agent (``copilot-swe-agent[bot]``) instead of (or
ahead of) running its own pipeline. The cloud agent produces a *plan draft* PR
(requirements doc, decomposition, Task Contract v2 skeleton); PFactory then runs
its governance/review gates on that PR.

Design notes:
- Reuses the ``gh`` CLI token already configured on the host — no new PAT.
- Opt-in via ``PFACTORY_COPILOT_DISPATCH_ENABLED`` (default off). When disabled
  or when dispatch fails (e.g. token lacks the Copilot scope), callers fall back
  to the normal PFactory flow; the warning is surfaced, never silently swallowed.
- The ``gh`` invocation is injected (``runner``) so unit tests never shell out.

Mirrors AIFactory's CopilotDispatchService (AIFactory#458) re-skinned to the
PFactory planning domain.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone

from factory_common.logsafe import sanitize_log

logger = logging.getLogger(__name__)

# Type of the injectable gh runner: (args) -> CompletedProcess
GhRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_gh_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
    """Run ``gh <args>`` capturing text output (30s timeout)."""
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CopilotDispatchService:
    """Assign PFactory planning issues to the Copilot cloud agent and watch
    for the resulting plan-draft PR."""

    AGENT_HANDLE = "copilot-swe-agent[bot]"
    DISPATCH_LABEL = "copilot:delegate"
    ENV_FLAG = "PFACTORY_COPILOT_DISPATCH_ENABLED"

    def __init__(self, runner: GhRunner | None = None) -> None:
        self._run = runner or _default_gh_runner

    # -- configuration -----------------------------------------------------

    @classmethod
    def is_enabled(cls) -> bool:
        """True when dispatch is opted-in via env (default off)."""
        return os.environ.get(cls.ENV_FLAG, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def has_dispatch_label(labels: list[str]) -> bool:
        return CopilotDispatchService.DISPATCH_LABEL in (labels or [])

    # -- dispatch ----------------------------------------------------------

    def dispatch(self, repo_full_name: str, issue_number: int) -> dict:
        """Assign ``issue_number`` to the Copilot cloud agent via gh.

        Returns a dispatch-metadata dict suitable for persisting onto the
        session/task. Raises RuntimeError if the gh call fails so the caller
        can fall back to the normal flow and surface the warning.
        """
        result = self._run(
            [
                "api",
                "--method",
                "PATCH",
                f"/repos/{repo_full_name}/issues/{issue_number}",
                "-f",
                f"assignees[]={self.AGENT_HANDLE}",
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Copilot dispatch failed for {repo_full_name}#{issue_number}: "
                f"{(result.stderr or '').strip()}"
            )
        logger.info(
            "[copilot-dispatch] assigned %s#%s to %s",
            sanitize_log(repo_full_name),
            sanitize_log(issue_number),
            sanitize_log(self.AGENT_HANDLE),
        )
        return {
            "enabled": True,
            "dispatched_at": _utcnow_iso(),
            "issue_number": issue_number,
            "repo": repo_full_name,
            "agent_handle": self.AGENT_HANDLE,
            "pr_number": None,
            "pr_url": None,
            "reviewed": False,
        }

    def find_copilot_pr(self, repo_full_name: str, issue_number: int) -> int | None:
        """Poll for a PR opened by the Copilot agent referencing ``issue_number``.

        GitHub returns ``user.login == "copilot-swe-agent"`` (no ``[bot]`` suffix)
        with ``user.type == "Bot"``; the ``[bot]`` suffix only appears in the
        display name. The filter therefore matches the login prefix + Bot type.
        """
        result = self._run(
            [
                "api",
                f"/repos/{repo_full_name}/pulls",
                "--jq",
                '[.[] | select(.user.type == "Bot" '
                'and (.user.login | startswith("copilot-swe-agent")) '
                f'and ((.body // "") | contains("#{issue_number}")))] '
                "| first | .number",
            ]
        )
        if result.returncode != 0:
            logger.warning(
                "[copilot-dispatch] PR poll failed for %s#%s: %s",
                sanitize_log(repo_full_name),
                sanitize_log(issue_number),
                sanitize_log((result.stderr or "").strip()),
            )
            return None
        number = (result.stdout or "").strip()
        if number and number != "null":
            try:
                return int(number)
            except ValueError:
                return None
        return None


# Module-level singleton the route layer shares.
SERVICE = CopilotDispatchService()
