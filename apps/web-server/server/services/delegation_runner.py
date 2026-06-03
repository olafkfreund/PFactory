"""Run the Copilot delegation flow for a prepared spec.

Shared by ``auto_fix_service.start_auto_fix`` and the wizard task-start
route (``routes/execution.py::start_task``). Both paths converge here so
there's exactly one place that knows how to:

1. Spawn the planner-only subprocess.
2. **Await** that subprocess to completion (gap #3 from issue #144 — the
   original V1-B inline path returned immediately after spawn, which meant
   the enrichment comment was rendered before ``test_plan.json``
   was written).
3. Render the PFactory enrichment comment.
4. Skip if a previous run already posted an enrichment comment on the same
   issue (bonus dedupe from #144).
5. Post the comment + assign Copilot.
6. Persist the queue/task state + emit a ``task:status`` event.

Caller is responsible for having already verified that the project's
``gitProvider == "github"`` and that the task wants delegation. This
helper does not gate on either — it assumes the policy decision is done.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Marker the formatter emits on every enrichment comment. We grep for it to
# detect a prior delegation run on the same issue so we don't double-post.
ENRICHMENT_MARKER = "## ✨ PFactory enrichment"

# How long to wait for the planner subprocess to write
# test_plan.json before giving up and posting whatever we have.
# 5 min covers the 95p planner runtime; longer is rare enough that we
# accept a degraded comment over an indefinite hang.
PLANNER_TIMEOUT_SECONDS = 300


async def run_delegation(
    *,
    project_id: str,
    project_path: Path,
    spec_id: str,
    issue_number: int,
    provider: Any,  # GitProvider — typed loosely to avoid the import dance
) -> dict[str, Any]:
    """Execute the Copilot delegation flow for the given spec + issue.

    Returns a dict describing the outcome: ``{"status": "delegated", ...}``
    on success; ``{"status": "delegated", "warning": "..."}`` if a step
    degraded but the flow still completed (e.g. comment failed to post but
    Copilot was assigned).
    """
    from ..services.agent_service import get_agent_service
    from ..websockets.events import broadcast_event, emit_task_status
    from .delegation_formatter import render_plan_as_comment

    task_id = f"{project_id}:{spec_id}"
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    # ------------------------------------------------------------------
    # 1. Spawn the planner-only subprocess and AWAIT it (gap #3).
    # ------------------------------------------------------------------
    await emit_task_status(task_id, "planning")
    agent_service = get_agent_service()
    try:
        proc = await agent_service.start_task_execution(
            task_id=task_id,
            project_path=project_path,
            spec_id=spec_id,
            auto_continue=True,
            force=True,
            stop_after_planning=True,
        )
    except ValueError as e:
        # "Already running" → another caller is already planning. Wait for
        # the running process to finish before reading the plan file.
        if "already running" in str(e):
            proc = agent_service.running_tasks.get(task_id)
        else:
            raise

    if proc is not None:
        try:
            await asyncio.wait_for(proc.wait(), timeout=PLANNER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                "[delegation_runner] planner timed out after %ds task=%s — "
                "posting comment with whatever was written so far",
                PLANNER_TIMEOUT_SECONDS,
                task_id,
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    # ------------------------------------------------------------------
    # 2. Render the enrichment comment.
    # ------------------------------------------------------------------
    plan_md = render_plan_as_comment(
        spec_dir / "test_plan.json",
        spec_dir / "spec.md",
    )

    # ------------------------------------------------------------------
    # 3. Dedupe — skip if a previous run already posted the marker.
    # ------------------------------------------------------------------
    posted = False
    skipped_dup = False
    try:
        existing = await _existing_enrichment_comment(provider, issue_number)
        if existing is not None:
            logger.info(
                "[delegation_runner] enrichment comment already exists on "
                "issue=%d (id=%s) — skipping re-post",
                issue_number,
                existing,
            )
            skipped_dup = True
        else:
            await provider.add_comment(issue_number, plan_md)
            posted = True
    except Exception as e:
        logger.warning(
            "[delegation_runner] comment post failed project=%s issue=%d err=%s",
            project_id,
            issue_number,
            e,
        )

    # ------------------------------------------------------------------
    # 4. Assign Copilot.
    # ------------------------------------------------------------------
    assigned = False
    try:
        await provider.assign_to_user(issue_number, ["Copilot"])
        assigned = True
    except NotImplementedError:
        logger.warning(
            "[delegation_runner] provider does not support assign_to_user; "
            "skipping Copilot assignment (project=%s)",
            project_id,
        )

    # ------------------------------------------------------------------
    # 5. Emit status + broadcast event.
    # ------------------------------------------------------------------
    delegated_at = datetime.now(timezone.utc).isoformat()
    await emit_task_status(task_id, "delegated")
    await broadcast_event(
        "auto_fix:delegated",
        {
            "projectId": project_id,
            "issueNumber": issue_number,
            "specId": spec_id,
            "delegateAgent": "github-copilot",
        },
    )

    return {
        "specId": spec_id,
        "taskId": task_id,
        "status": "delegated",
        "delegatedAt": delegated_at,
        "commentPosted": posted,
        "commentSkippedAsDuplicate": skipped_dup,
        "copilotAssigned": assigned,
    }


async def _existing_enrichment_comment(
    provider: Any, issue_number: int
) -> int | None:
    """Return the comment ID of any prior PFactory enrichment comment
    on this issue, or None.

    Uses the provider's low-level api_get (defined on the GitProvider
    Protocol). We intentionally don't expose this as a Protocol method
    because the lookup is delegation-specific.
    """
    repo = getattr(provider, "repo", None)
    if not repo:
        return None
    try:
        comments = await provider.api_get(f"/repos/{repo}/issues/{issue_number}/comments")
    except Exception as e:
        logger.debug(
            "[delegation_runner] could not list comments on issue=%d: %s",
            issue_number,
            e,
        )
        return None
    if not isinstance(comments, list):
        return None
    for c in comments:
        body = c.get("body") or ""
        if ENRICHMENT_MARKER in body:
            return c.get("id")
    return None
