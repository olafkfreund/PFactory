"""
Worktree-sync and skill-context mixin for AgentService.

Syncs spec artifacts from the task worktree back to the main spec dir (with
real-time subtask/progress WebSocket emission) and writes ``skill_context.md``
from the task's selected skills. Extracted verbatim from ``agent_service.py``
(issue Factory#255 seam d); ``AgentService`` inherits this mixin, so behavior
is unchanged.

The per-line ``noqa`` directives mark pre-existing violations carried over
verbatim (behavior-preserving move; legacy is fixed on touch per the ratchet
policy, not during extraction).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.services.git_utils import safe_spec_component

from ..websockets.events import emit_subtask_update  # noqa: TID252
from .task_models import TaskPhase, scale_progress


class AgentWorktreeSyncMixin:
    """Worktree-sync + skill-context behavior mixed into ``AgentService``.

    Host-class dependencies (attributes and helpers defined on
    ``AgentService``) are declared here for type checkers only.
    """

    if TYPE_CHECKING:
        _task_build_progress_offset: dict[str, int]
        _task_subtask_states: dict[str, dict[str, str]]
        _task_current_phases: dict[str, TaskPhase]

        async def _safe_emit_task_update(
            self, task_id: str, payload: dict[str, Any], *, force: bool = False
        ) -> None: ...

    async def _sync_worktree_files(  # noqa: PLR0912, PLR0915
        self, project_path: Path, spec_id: str, task_id: str | None = None
    ) -> None:
        """Sync files from worktree spec dir to main spec dir for frontend visibility.

        Args:
            project_path: Path to the project
            spec_id: Spec directory name (e.g., "001-fix-bug")
            task_id: Full task ID (project_id:spec_id) for consistent tracking.
                Falls back to spec_id if not provided.
        """
        # Use task_id for tracking if provided, otherwise fall back to spec_id for backwards compatibility  # noqa: E501
        # The component is joined onto the project root below and then read
        # from / written to, so it is validated here at the entry point rather
        # than at each of the joins (#335).
        spec_id = safe_spec_component(spec_id)
        tracking_key = task_id or spec_id
        import logging  # noqa: PLC0415

        logger = logging.getLogger(__name__)

        # Paths
        worktree_spec = (
            project_path
            / ".pfactory"
            / "worktrees"
            / "tasks"
            / spec_id
            / ".pfactory"
            / "specs"
            / spec_id
        )
        main_spec = project_path / ".pfactory" / "specs" / spec_id

        # Ensure main spec dir exists
        main_spec.mkdir(parents=True, exist_ok=True)

        # Files to sync (in order of priority)
        files_to_sync = [
            "test_plan.json",  # Most critical for UI
            "task_logs.json",  # Detailed phase logs for UI
            "build-progress.txt",
            "context.json",
            "qa_report.md",
            "QA_FIX_REQUEST.md",
            "spec.md",
            "requirements.json",
        ]

        # Directories to sync (will copy entire directory tree)
        dirs_to_sync = [
            "memory",  # Session insights and memory data
        ]

        synced_count = 0
        for filename in files_to_sync:
            src = worktree_spec / filename
            dst = main_spec / filename
            if src.exists():
                try:
                    # For test_plan.json, preserve status and reviewReason from main spec
                    # These fields are set by _update_plan_status and shouldn't be overwritten
                    if filename == "test_plan.json" and dst.exists():
                        try:
                            main_plan = json.loads(dst.read_text())
                            worktree_plan = json.loads(src.read_text())

                            # Preserve top-level fields from main spec
                            preserved_status = main_plan.get("status")
                            preserved_reason = main_plan.get("reviewReason")

                            # Build map of main spec subtask statuses
                            STATUS_ORDER = {  # noqa: N806
                                "pending": 0,
                                "in_progress": 1,
                                "completed": 2,
                                "failed": 2,
                            }
                            main_subtask_statuses = {}
                            for phase in main_plan.get("phases", []):
                                for subtask in phase.get("subtasks", []):
                                    sid = subtask.get("id")
                                    if sid:
                                        main_subtask_statuses[sid] = subtask.get(
                                            "status", "pending"
                                        )

                            # Start from worktree plan (has latest structure)
                            merged_plan = worktree_plan

                            # Restore preserved top-level fields
                            if preserved_status:
                                merged_plan["status"] = preserved_status
                            if preserved_reason:
                                merged_plan["reviewReason"] = preserved_reason

                            # Prevent subtask status regressions
                            for phase in merged_plan.get("phases", []):
                                for subtask in phase.get("subtasks", []):
                                    sid = subtask.get("id")
                                    if sid and sid in main_subtask_statuses:
                                        main_rank = STATUS_ORDER.get(main_subtask_statuses[sid], 0)
                                        wt_rank = STATUS_ORDER.get(
                                            subtask.get("status", "pending"), 0
                                        )
                                        if main_rank > wt_rank:
                                            subtask["status"] = main_subtask_statuses[sid]

                            dst.write_text(json.dumps(merged_plan, indent=2))
                        except (json.JSONDecodeError, OSError) as merge_err:
                            logger.warning(
                                f"[AgentService] Failed to merge test_plan.json, falling back to copy: {merge_err}"  # noqa: E501
                            )
                            shutil.copy2(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    synced_count += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[AgentService] Failed to sync {filename}: {e}")

        # Sync any additional files created by the agent (e.g., plan .md files)
        # that aren't in the hardcoded list
        try:
            known_files = set(files_to_sync)
            for src_file in worktree_spec.iterdir():
                if src_file.is_file() and src_file.name not in known_files:
                    try:
                        shutil.copy2(src_file, main_spec / src_file.name)
                        synced_count += 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[AgentService] Failed to sync extra file {src_file.name}: {e}"
                        )
        except OSError as e:
            logger.warning(f"[AgentService] Failed to scan worktree spec dir for extra files: {e}")

        # Sync directories
        for dirname in dirs_to_sync:
            src_dir = worktree_spec / dirname
            dst_dir = main_spec / dirname
            if src_dir.exists() and src_dir.is_dir():
                try:
                    # Remove existing and copy fresh
                    if dst_dir.exists():
                        shutil.rmtree(dst_dir)
                    shutil.copytree(src_dir, dst_dir)
                    synced_count += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[AgentService] Failed to sync directory {dirname}: {e}")

        if synced_count > 0:
            logger.debug(
                f"[AgentService] Synced {synced_count} files from worktree to main spec dir"
            )

        # Tier B auto-reload — stream new build-progress.txt lines as task:log
        # events.  The agent appends a human-readable narrative ("Starting
        # phase 1: PROJECT DISCOVERY", "Discovered 22 files", "Working on
        # 1.1 — ...") that, until now, only the full-page-reload `getTask`
        # endpoint surfaced.  Tailing the delta on each sync tick lets the
        # kanban detail view scroll the narrative in real time.
        if task_id:
            try:
                bp_main = main_spec / "build-progress.txt"
                if bp_main.exists():
                    current_size = bp_main.stat().st_size
                    prev_offset = self._task_build_progress_offset.get(task_id, 0)
                    # If the file was truncated/restarted, reset to 0 rather
                    # than re-reading nonsense from a stale offset.
                    if current_size < prev_offset:
                        prev_offset = 0
                    if current_size > prev_offset:
                        with bp_main.open("r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(prev_offset)
                            new_text = fh.read()
                        self._task_build_progress_offset[task_id] = current_size
                        # Emit one task:log per non-empty line so the frontend
                        # batches them at its 16-ms tick (useIpc.ts:191).
                        from ..websockets.events import emit_task_log  # noqa: PLC0415, TID252

                        for line in new_text.splitlines():
                            stripped = line.rstrip()
                            if stripped:
                                await emit_task_log(task_id, stripped)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[AgentService] build-progress tail emit failed: {e}")

        # Always check for subtask status changes and emit WebSocket updates
        # This runs independently of file sync to ensure real-time updates
        try:
            # Read implementation plan for progress info
            plan_file = main_spec / "test_plan.json"
            if plan_file.exists():
                plan = json.loads(plan_file.read_text())

                # Calculate progress from subtasks in phases
                all_subtasks = []
                current_phase = None
                for phase in plan.get("phases", []):
                    if phase.get("status") == "in_progress":
                        current_phase = phase.get("name")
                    all_subtasks.extend(phase.get("subtasks", []))

                completed = sum(1 for s in all_subtasks if s.get("status") == "completed")
                total = len(all_subtasks)
                progress = int((completed / total) * 100) if total > 0 else 0

                # Find current subtask
                current_subtask = None
                for s in all_subtasks:
                    if s.get("status") == "in_progress":
                        current_subtask = s.get("description", s.get("id"))
                        break

                # Build subtasks array for real-time frontend updates
                subtasks_data = [
                    {"id": s.get("id"), "status": s.get("status")} for s in all_subtasks
                ]

                # Detect individual subtask status changes and emit granular events
                # This enables real-time subtask checkbox updates in the frontend
                previous_states = self._task_subtask_states.get(tracking_key, {})
                current_states = {s.get("id"): s.get("status") for s in all_subtasks}

                # Check for changes and emit individual events
                has_changes = False
                for subtask_id, current_status in current_states.items():
                    previous_status = previous_states.get(subtask_id)
                    if previous_status != current_status:
                        has_changes = True
                        # Subtask status changed - emit granular event
                        # Use task_id (projectId:specId format) so frontend can match
                        await emit_subtask_update(
                            task_id=task_id or spec_id,
                            subtask_id=subtask_id,
                            status=current_status,
                            previous_status=previous_status,
                        )

                # Update tracking for next comparison
                self._task_subtask_states[tracking_key] = current_states

                # Emit task update if subtasks changed OR worktree files were
                # synced. The ``force`` flag tells _safe_emit_task_update to
                # bypass the structural dedup when ``synced_count > 0`` —
                # otherwise long subtasks where phase/progress/subtask-status
                # haven't moved yet would suppress every 3-sec heartbeat and
                # the kanban board freezes. Frontend's updateExecutionProgress
                # is idempotent for identical payloads, so the cost is minimal.
                if has_changes or synced_count > 0:
                    # Use the actual current execution phase from phase event tracking
                    actual_phase = (
                        self._task_current_phases.get(task_id, TaskPhase.PLANNING).value
                        if task_id
                        else "coding"
                    )
                    await self._safe_emit_task_update(
                        task_id or spec_id,
                        {
                            "executionProgress": {
                                "phase": actual_phase,
                                "phaseProgress": progress,
                                "overallProgress": scale_progress(actual_phase, progress),
                                "currentSubtask": current_subtask,
                                "message": f"{completed}/{total} subtasks completed",
                            },
                            "phase": current_phase,
                            "subtasksCompleted": completed,
                            "subtasksTotal": total,
                            "subtasks": subtasks_data,
                        },
                        # Sync ticks always go through: file CONTENT may have
                        # changed even if the dedup signature didn't.
                        force=synced_count > 0,
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[AgentService] Failed to emit task update: {e}")

    def _write_skill_context(self, spec_dir: Path) -> None:  # noqa: PLR0912, PLR0915
        """Write skill_context.md to spec_dir based on selectedSkills in task_metadata.json.

        If selectedSkills is non-empty, loads up to 5 skill files and writes them
        as a structured markdown file that the agent system will auto-include as
        context (the agent reads all .md files in spec_dir).

        If no skills are selected, removes any existing skill_context.md.
        """
        import logging  # noqa: PLC0415

        logger = logging.getLogger(__name__)

        skill_context_file = spec_dir / "skill_context.md"
        task_metadata_file = spec_dir / "task_metadata.json"

        # Load task metadata to get selected skills
        selected_skill_ids: list[str] = []
        if task_metadata_file.exists():
            try:
                task_metadata = json.loads(task_metadata_file.read_text())
                raw_skills = task_metadata.get("selectedSkills", [])
                # selectedSkills is stored as list[dict] with {id, name, category, source}
                # Also handle plain string IDs for backward compatibility
                for item in raw_skills:
                    if isinstance(item, dict):  # noqa: SIM108
                        sid = item.get("id", "")
                    else:
                        sid = str(item)
                    if sid:
                        selected_skill_ids.append(sid)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[AgentService] Could not read task_metadata.json for skills: {e}")

        # If no skills selected, remove any existing skill_context.md
        if not selected_skill_ids:
            if skill_context_file.exists():
                try:
                    skill_context_file.unlink()
                    logger.info("[AgentService] Removed skill_context.md (no skills selected)")
                except OSError as e:
                    logger.warning(f"[AgentService] Could not remove skill_context.md: {e}")
            return

        # Load skill contents (max 5 skills to stay within token budget)
        from .skills_service import get_skills_service  # noqa: PLC0415

        skills_service = get_skills_service()

        sections: list[str] = []
        loaded_count = 0

        for skill_id in selected_skill_ids[:5]:
            # Parse skill_id format: "{category}/{skill_name}"
            if "/" not in skill_id:
                logger.warning(f"[AgentService] Invalid skill_id format (missing '/'): {skill_id}")
                continue

            category, name = skill_id.split("/", 1)
            skill_summary = skills_service.get_skill(category, name)
            skill_content = skills_service.get_skill_content(category, name)

            if skill_content is None:
                logger.warning(f"[AgentService] Skill not found in index: {skill_id}")
                continue

            # Truncate each skill to 2500 chars to manage token budget
            skill_content_truncated = skill_content[:2500]
            if len(skill_content) > 2500:  # noqa: PLR2004
                skill_content_truncated += "\n\n*[Content truncated for token budget]*"

            display_name = skill_summary.name if skill_summary else name
            sections.append(f"## {display_name} ({category})\n\n{skill_content_truncated}\n\n---")
            loaded_count += 1

        if not sections:
            # No skills could be loaded — clean up stale file if present
            if skill_context_file.exists():
                try:  # noqa: SIM105
                    skill_context_file.unlink()
                except OSError:
                    pass
            return

        # Format as structured markdown
        header = (
            "# Selected Skills Context\n\n"
            "The following skill documentation has been included to assist with this task.\n"
            "Reference these skills when implementing the solution.\n\n"
            "---"
        )
        skill_context_content = header + "\n\n" + "\n\n".join(sections) + "\n"

        try:
            spec_dir.mkdir(parents=True, exist_ok=True)
            skill_context_file.write_text(skill_context_content, encoding="utf-8")
            logger.info(f"[AgentService] Wrote skill_context.md with {loaded_count} skill(s)")
        except OSError as e:
            logger.error(f"[AgentService] Failed to write skill_context.md: {e}")
