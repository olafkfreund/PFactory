"""Track delegated tasks for the resulting Copilot PR.

Runs as part of the existing 5-minute Auto-Fix poll cycle (the frontend
hook ``useAutoFix.ts`` triggers ``POST /api/projects/{id}/auto-fix/check-new``).
For every queue item with status ``delegated``:

1. Search the provider for open PRs by Copilot's bot login that
   reference the originating issue number.
2. If a match is found: transition the queue item to ``in_review``,
   persist the resulting PR number, emit a ``task:status`` WebSocket
   event so the UI updates without a refresh.
3. If 24 hours have elapsed with no matching PR: transition the queue
   item to ``declined`` and emit a notice so the user can choose to
   re-run with the local agent.

The tracker is provider-aware only for GitHub today. GitLab Duo
delegation lands in V1.5 (#98); ADO is permanently unsupported.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Copilot Coding Agent posts PRs under one of these logins. We accept
# either spelling to be resilient to GitHub renaming the bot.
COPILOT_PR_AUTHORS = frozenset(
    {
        "copilot-swe-agent",
        "github-copilot[bot]",
        "copilot-swe-agent[bot]",
        "copilot",
    }
)

# GitLab Duo Workflow posts MRs under one of these usernames. The exact
# username may shift across GitLab versions; we accept the common forms.
GITLAB_DUO_MR_AUTHORS = frozenset(
    {
        "gitlab-duo",
        "gitlab-duo[bot]",
        "gitlab-bot",
        "duo",
    }
)

# Unified set used by the per-PR matcher — it doesn't care which vendor
# authored the PR, only that it's an AI-bot we delegated to.
_DELEGATED_PR_AUTHORS = COPILOT_PR_AUTHORS | GITLAB_DUO_MR_AUTHORS

DECLINE_AFTER_HOURS = 24


async def scan_delegated_tasks(project_id: str) -> dict[str, Any]:
    """Inspect every delegated queue item and advance its status if possible.

    Returns a summary of transitions made this pass — useful for the
    caller (``check_new_and_start_all``) to surface in logs/responses.
    """
    from .auto_fix_service import _provider_for, _upsert_queue_item, get_queue

    queue = get_queue(project_id)
    delegated = [q for q in queue if q.get("status") == "delegated"]
    if not delegated:
        return {"checked": 0, "promoted": [], "declined": []}

    try:
        provider = _provider_for(project_id)
    except Exception as e:
        logger.warning(
            "[delegation_tracker] provider unavailable project=%s err=%s",
            project_id,
            e,
        )
        return {"checked": 0, "promoted": [], "declined": [], "error": str(e)}

    # GitHub Copilot (V1) and GitLab Duo Workflow (V1.5) are both wired.
    # Azure DevOps has no autonomous agent equivalent — skip with a notice.
    provider_type_str = str(getattr(provider, "provider_type", "")).lower()
    is_supported = (
        provider_type_str.endswith("github") or provider_type_str.endswith("gitlab")
    )
    if not is_supported:
        return {
            "checked": len(delegated),
            "promoted": [],
            "declined": [],
            "skipped": f"unsupported provider: {provider_type_str}",
        }

    promoted: list[dict[str, Any]] = []
    declined: list[dict[str, Any]] = []

    try:
        from runners.github.providers.protocol import PRFilters
        open_prs = await provider.fetch_prs(PRFilters(state="open", limit=200))
    except Exception as e:
        logger.warning(
            "[delegation_tracker] fetch_prs failed project=%s err=%s",
            project_id,
            e,
        )
        return {"checked": len(delegated), "promoted": [], "declined": [], "error": str(e)}

    now = datetime.now(timezone.utc)

    for item in delegated:
        issue_number = item.get("issueNumber")
        if issue_number is None:
            continue

        match = _find_copilot_pr_for_issue(open_prs, issue_number)
        if match is not None:
            updated = dict(item)
            updated["status"] = "in_review"
            updated["prNumber"] = match["number"]
            updated["prUrl"] = match["url"]
            updated["updatedAt"] = now.isoformat()
            _upsert_queue_item(project_id, updated)
            promoted.append(
                {
                    "issueNumber": issue_number,
                    "prNumber": match["number"],
                    "prUrl": match["url"],
                }
            )
            await _emit_status(project_id, item, "in_review", pr_number=match["number"])
            continue

        # No match yet — check the decline window.
        delegated_at = _parse_iso(item.get("delegatedAt") or item.get("updatedAt"))
        if delegated_at is not None and now - delegated_at >= timedelta(
            hours=DECLINE_AFTER_HOURS
        ):
            updated = dict(item)
            updated["status"] = "declined"
            updated["declinedAt"] = now.isoformat()
            updated["updatedAt"] = now.isoformat()
            _upsert_queue_item(project_id, updated)
            declined.append({"issueNumber": issue_number})
            await _emit_status(project_id, item, "declined")

    logger.info(
        "[delegation_tracker] scan project=%s delegated=%d promoted=%d declined=%d",
        project_id,
        len(delegated),
        len(promoted),
        len(declined),
    )
    return {"checked": len(delegated), "promoted": promoted, "declined": declined}


def _find_copilot_pr_for_issue(
    open_prs: list[Any], issue_number: int
) -> dict[str, Any] | None:
    """Return the first open PR/MR that (a) was authored by an AI bot
    we delegated to (Copilot OR GitLab Duo) and (b) references
    ``#{issue_number}`` in its title or body.

    The PR objects come from ``GitProvider.fetch_prs`` so they expose
    ``.author``, ``.title``, ``.body``, ``.number`` and ``.url`` per the
    ``PRData`` dataclass — same shape for GitHub and GitLab.
    """
    needle = f"#{issue_number}"
    for pr in open_prs:
        author = (getattr(pr, "author", "") or "").lower()
        if not any(author == a or author == a.lower() for a in _DELEGATED_PR_AUTHORS):
            continue
        title = getattr(pr, "title", "") or ""
        body = getattr(pr, "body", "") or ""
        if needle in title or needle in body:
            return {
                "number": getattr(pr, "number", 0),
                "url": getattr(pr, "url", ""),
            }
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Python tolerates the "Z" suffix only via fromisoformat in 3.11+.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _emit_status(
    project_id: str,
    item: dict[str, Any],
    new_status: str,
    pr_number: int | None = None,
) -> None:
    """Broadcast a ``task:status`` event for the delegated item."""
    spec_id = item.get("specId")
    if not spec_id:
        return
    try:
        from ..websockets.events import emit_task_status

        task_id = f"{project_id}:{spec_id}"
        await emit_task_status(task_id, new_status)
        if pr_number is not None:
            logger.info(
                "[delegation_tracker] task %s → %s (PR #%d)",
                task_id,
                new_status,
                pr_number,
            )
    except Exception as e:  # pragma: no cover — never let WebSocket errors abort tracking
        logger.warning(
            "[delegation_tracker] emit_status failed project=%s spec=%s err=%s",
            project_id,
            spec_id,
            e,
        )
