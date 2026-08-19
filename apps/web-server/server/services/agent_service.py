"""
Agent execution service.

Wraps the existing run.py and spec_runner.py CLI tools as async services,
enabling task execution with real-time streaming of logs and progress.
"""

import asyncio
import json
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from factory_common.logsafe import sanitize_log
from server.services.git_utils import safe_spec_component

from ..config import get_settings
from ..utils.subprocess_env import make_subprocess_env
from ..websockets.events import (
    emit_task_status,
    emit_task_update,
)
from .agent_failover import AgentFailoverMixin
from .agent_process_monitor import AgentProcessMonitorMixin
from .agent_worktree_sync import AgentWorktreeSyncMixin

# TaskLogWriter moved to task_log_writer.py (Factory#255 seam b); re-exported
# here so existing import paths keep working unchanged.
from .task_log_writer import TaskLogWriter as TaskLogWriter  # noqa: PLC0414

# Re-export the task-model cluster so all existing import paths keep working
# unchanged. The ``X as X`` form is the mypy-standard explicit re-export idiom
# (satisfies no_implicit_reexport); PLC0414 is suppressed per-line because the
# rule fires on non-renaming aliases but the pattern IS intentional here.
from .task_models import (
    PHASE_RANGES as PHASE_RANGES,  # noqa: PLC0414
    TaskLog as TaskLog,  # noqa: PLC0414
    TaskPhase as TaskPhase,  # noqa: PLC0414
    TaskProgress as TaskProgress,  # noqa: PLC0414
    _dedup_signature as _dedup_signature,  # noqa: PLC0414
    phase_to_review_reason as phase_to_review_reason,  # noqa: PLC0414
    phase_to_status as phase_to_status,  # noqa: PLC0414
    scale_progress as scale_progress,  # noqa: PLC0414
)


class AgentService(AgentFailoverMixin, AgentWorktreeSyncMixin, AgentProcessMonitorMixin):
    """Service for executing AI agents on tasks."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.running_tasks: dict[str, asyncio.subprocess.Process] = {}
        self._log_callbacks: dict[str, list[Callable]] = {}
        self._progress_callbacks: dict[str, list[Callable]] = {}
        self._task_log_writers: dict[str, tuple[TaskLogWriter, TaskLogWriter]] = {}
        # Per-spec stderr capture file paths (#146).
        self._spec_stderr_logs: dict[str, Path] = {}
        # Track sequence numbers per task for frontend out-of-order detection
        self._task_sequence_numbers: dict[str, int] = {}
        # Issue #14 — last emitted task:update signature per task. Used by
        # _safe_emit_task_update to suppress identical re-emissions (e.g. the
        # 3-second periodic _sync_worktree_files tick during long phases).
        self._last_emitted_task_update: dict[str, tuple] = {}
        # Track task start times for UI display
        self._task_start_times: dict[str, str] = {}
        # Track user IDs per task for email notifications
        self._task_user_ids: dict[str, str] = {}
        # Track current execution phase per task (for proper phase status on completion)
        self._task_current_phases: dict[str, TaskPhase] = {}
        # Track which Claude profile each task is using (for reactive failover)
        self._task_profiles: dict[str, dict] = {}
        # Track rate limit detection per task to allow reactive failover
        self._task_rate_limits: dict[str, bool] = {}
        # Track previous subtask statuses per task for granular change detection
        # Format: {task_id: {subtask_id: status_string}}
        self._task_subtask_states: dict[str, dict[str, str]] = {}
        # Track spec directory per task for reading implementation plans
        self._spec_dirs: dict[str, Path] = {}
        # Track tasks that were manually stopped (to prevent _monitor_process from re-handling)
        self._task_stopped: set[str] = set()
        # Track byte offset into build-progress.txt per task so the periodic
        # worktree-sync tick can emit only NEW lines as task:log events. Lets
        # the kanban detail view scroll the agent's narrative in real time
        # rather than waiting for full-page reload (Tier B auto-reload).
        self._task_build_progress_offset: dict[str, int] = {}

    @property
    def backend_path(self) -> Path:
        """Get path to the backend directory."""
        return Path(self.settings.BACKEND_PATH)

    def register_log_callback(self, task_id: str, callback: Callable) -> Callable:
        """Register a callback for task logs. Returns unregister function."""
        if task_id not in self._log_callbacks:
            self._log_callbacks[task_id] = []
        self._log_callbacks[task_id].append(callback)
        return lambda: self._log_callbacks.get(task_id, []).remove(callback)

    def register_progress_callback(self, task_id: str, callback: Callable) -> Callable:
        """Register a callback for task progress. Returns unregister function."""
        if task_id not in self._progress_callbacks:
            self._progress_callbacks[task_id] = []
        self._progress_callbacks[task_id].append(callback)
        return lambda: self._progress_callbacks.get(task_id, []).remove(callback)

    async def _emit_log(self, log: TaskLog) -> None:
        """Emit a log to all registered callbacks."""
        callbacks = self._log_callbacks.get(log.task_id, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(log)
                else:
                    callback(log)
            except Exception:
                pass

    def _get_next_sequence_number(self, task_id: str) -> int:
        """Get the next sequence number for a task (for out-of-order detection)."""
        current = self._task_sequence_numbers.get(task_id, 0)
        next_seq = current + 1
        self._task_sequence_numbers[task_id] = next_seq
        return next_seq

    def _get_current_phase(self, task_id: str) -> TaskPhase:
        """Get the current execution phase for a task.

        Returns the tracked phase or defaults to PLANNING if unknown.
        This is used to determine which phase to mark as completed/failed
        when a task finishes, avoiding incorrect status on phases that were
        never actually reached.
        """
        return self._task_current_phases.get(task_id, TaskPhase.PLANNING)

    async def _safe_emit_task_update(
        self, task_id: str, payload: dict, *, force: bool = False
    ) -> None:
        """Funnel for all in-service task:update emissions with structural dedup.

        Compares the payload's structural signature (phase, progress, subtasks,
        etc. — see ``_dedup_signature``) against the last emission for this
        task. If identical, the emit is suppressed and we log at DEBUG.

        ``force=True`` bypasses the dedup check and always broadcasts. Use it
        from the periodic worktree-sync tick when we know files were just
        copied (the file CONTENT may have changed even though the structural
        signature didn't — e.g. ``task_logs.json`` grew, ``build-progress.txt``
        was rewritten, qwen3 is mid-tool-loop inside a single subtask). Without
        this escape hatch the kanban board freezes for the entire duration
        of a long subtask because dedup correctly observes that phase/progress/
        subtask-status haven't moved yet.

        asyncio single-thread invariant: the comparison and the dict write are
        not separated by any ``await`` — no other coroutine can interleave on
        this event loop. If anyone ever moves these emissions to a thread
        pool, ``_last_emitted_task_update`` becomes a race and would need an
        ``asyncio.Lock``.
        """
        import logging

        _logger = logging.getLogger(__name__)
        sig = _dedup_signature(payload)
        if not force and self._last_emitted_task_update.get(task_id) == sig:
            _logger.debug(
                "[AgentService] dedup-suppressed task:update for %s", sanitize_log(task_id)
            )
            return
        self._last_emitted_task_update[task_id] = sig
        await emit_task_update(task_id, payload)

    async def _safe_emit_task_status(
        self, task_id: str, status: str, review_reason: str | None = None
    ) -> None:
        """Funnel for all in-service task:status emissions.

        No dedup — status transitions are rare and meaningful, and a duplicate
        is harmless (the frontend just reapplies the same column move). Kept
        as a helper for symmetry with _safe_emit_task_update and for future
        evolution (e.g. inserting metrics, alerting).
        """
        await emit_task_status(task_id, status, review_reason)

    async def _emit_progress(
        self, progress: TaskProgress, previous_phase: TaskPhase | None = None
    ) -> None:
        """Emit progress to all registered callbacks and broadcast via WebSocket.

        If previous_phase is provided and differs from current phase, also emits
        a status change event to update the kanban board column.
        """
        # Broadcast via WebSocket for real-time frontend updates
        try:
            # Use task:update event which frontend handles correctly for progress
            # Frontend's onTaskUpdate handler expects: {taskId, executionProgress?, phase?, subtasks?, ...}
            phase_progress = progress.percentage or 0
            phase_value = progress.phase.value if progress.phase else "coding"
            # Scale within-phase progress to overall range, unless explicitly overridden
            if progress.overall_progress is not None:
                overall_progress = progress.overall_progress
            else:
                overall_progress = scale_progress(phase_value, phase_progress)

            # Get sequence number for out-of-order detection
            sequence_number = self._get_next_sequence_number(progress.task_id)

            # Get task start time (tracked when task started)
            started_at = self._task_start_times.get(progress.task_id)

            # Read subtasks from test_plan.json for real-time UI updates
            # Frontend needs the full subtasks array to display checkboxes and status
            subtasks_data = []
            try:
                # Get spec directory from task metadata
                spec_dir = self._spec_dirs.get(progress.task_id)
                if spec_dir:
                    plan_file = spec_dir / "test_plan.json"
                    if plan_file.exists():
                        plan = json.loads(plan_file.read_text())
                        # Extract all subtasks from all phases
                        phases = plan.get("phases", [])
                        for phase in phases:
                            phase_subtasks = phase.get("subtasks", [])
                            for subtask in phase_subtasks:
                                subtasks_data.append(
                                    {
                                        "id": subtask.get("id", ""),
                                        "status": subtask.get("status", "pending"),
                                        "title": subtask.get("description", ""),
                                    }
                                )
            except Exception as e:
                import logging

                logging.getLogger(__name__).debug(
                    "[AgentService] Could not read subtasks for %s: %s",
                    sanitize_log(progress.task_id),
                    sanitize_log(e),
                )

            await self._safe_emit_task_update(
                progress.task_id,
                {
                    "executionProgress": {
                        "phase": phase_value,
                        "phaseProgress": phase_progress,
                        "overallProgress": overall_progress,
                        "currentSubtask": progress.subtask,
                        "message": progress.message,
                        "sequenceNumber": sequence_number,
                        "startedAt": started_at,
                    },
                    "phase": phase_value,
                    "subtasksCompleted": progress.subtask_index,
                    "subtasksTotal": progress.subtask_total,
                    "subtasks": subtasks_data,  # Include subtasks array for frontend
                },
            )

            # If phase changed, also emit status change for kanban column movement
            if previous_phase is not None and progress.phase != previous_phase:
                new_status = phase_to_status(progress.phase)
                review_reason = phase_to_review_reason(progress.phase)
                await self._safe_emit_task_status(progress.task_id, new_status, review_reason)

        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "[AgentService] WebSocket broadcast failed: %s", sanitize_log(e)
            )

        # Also emit to local callbacks
        callbacks = self._progress_callbacks.get(progress.task_id, [])
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(progress)
                else:
                    callback(progress)
            except Exception:
                pass

    async def _update_plan_status(
        self,
        project_path: Path,
        spec_id: str,
        status: str,
        task_id: str,
        *,
        emit_events: bool = True,
    ) -> None:
        """Update the status field in test_plan.json after task completion.

        Also emits WebSocket events so the frontend updates in real-time UNLESS
        ``emit_events=False`` is passed — used by ``_monitor_process`` at the
        terminal exit branch (Issue #14) where the subsequent ``_emit_progress``
        is the single canonical terminal emission. Mid-run callers
        (plan_review / human_review checkpoints) keep the default ``True`` so
        kanban gets subtask data immediately.
        """
        import logging

        logger = logging.getLogger(__name__)
        spec_id = safe_spec_component(
            spec_id
        )  # #335: barrier before path use (dominates plan_file)
        plan_file = project_path / ".pfactory" / "specs" / spec_id / "test_plan.json"
        logger.info(
            "[AgentService._update_plan_status] CALLED for spec_id=%s, status=%s, task_id=%s",
            sanitize_log(spec_id),
            sanitize_log(status),
            sanitize_log(task_id),
        )
        logger.info(
            "[AgentService._update_plan_status] plan_file path: %s", sanitize_log(plan_file)
        )
        logger.info(
            "[AgentService._update_plan_status] plan_file exists: %s",
            sanitize_log(plan_file.exists()),
        )
        if not plan_file.exists():
            logger.warning(
                "[AgentService._update_plan_status] plan_file does not exist, returning early"
            )
            return

        # Map internal status to frontend-compatible status using the canonical helpers
        # (defined before try so it's available in the except fallback)
        phase_enum_map = {
            "completed": TaskPhase.COMPLETED,
            "failed": TaskPhase.FAILED,
            "human_review": TaskPhase.PLAN_REVIEW,
        }
        phase_enum = phase_enum_map.get(status)

        try:
            plan = json.loads(plan_file.read_text())

            # Don't overwrite if user explicitly marked task as done via kanban
            if plan.get("status") == "done":
                logger.info(
                    "[AgentService._update_plan_status] Plan status is 'done' (user-set), skipping overwrite for %s",
                    sanitize_log(spec_id),
                )
                return

            # Fix 2: Validate that the plan is not just a minimal status object
            # A valid plan should have phases and subtasks from spec creation
            if "phases" not in plan or not plan.get("phases"):
                logger.error(
                    "[AgentService] Invalid or minimal implementation plan detected for %s",
                    sanitize_log(spec_id),
                )
                if emit_events:
                    await self._safe_emit_task_status(task_id, "failed", "invalid_plan")
                return
            if phase_enum:
                plan["status"] = phase_to_status(phase_enum)
                review_reason = phase_to_review_reason(phase_enum)
                if review_reason:
                    plan["reviewReason"] = review_reason
            else:
                plan["status"] = status

            logger.info(
                "[AgentService._update_plan_status] About to write file with status=%s, reviewReason=%s",
                sanitize_log(plan.get("status")),
                sanitize_log(plan.get("reviewReason")),
            )
            plan_file.write_text(json.dumps(plan, indent=2))
            logger.info("[AgentService._update_plan_status] Successfully wrote plan_file")
            logger.info(
                "[AgentService] Updated plan status to '%s' for %s",
                sanitize_log(plan["status"]),
                sanitize_log(spec_id),
            )

            # Extract subtasks for WebSocket broadcast
            subtasks_data = []
            phases = plan.get("phases", [])
            for phase in phases:
                phase_subtasks = phase.get("subtasks", [])
                for subtask in phase_subtasks:
                    subtasks_data.append(
                        {
                            "id": subtask.get("id", ""),
                            "status": subtask.get("status", "pending"),
                            "title": subtask.get("description", ""),
                        }
                    )

            # Emit WebSocket events so frontend updates in real-time. Skipped
            # at the terminal exit branch (Issue #14) — the _monitor_process
            # caller will emit a single canonical _emit_progress(COMPLETED|FAILED)
            # that fires both task:update and task:status itself.
            if emit_events:
                review_reason = plan.get("reviewReason")
                # First emit status change
                await self._safe_emit_task_status(task_id, plan["status"], review_reason)
                # Then emit task update with subtasks so they appear immediately
                # in UI. Payload is ENRICHED with an executionProgress block (Issue #14)
                # so the frontend's log doesn't render `phase: N/A` and the store
                # receives a coherent terminal phase value.
                completed_count = sum(1 for s in subtasks_data if s["status"] == "completed")
                # Use the caller-supplied `status` argument (the raw terminal
                # signal — "completed" / "failed") rather than the already-mapped
                # `plan["status"]` (which for completed tasks becomes
                # "human_review" via phase_to_status). The dedup-signature
                # consumers downstream want the raw phase value.
                terminal_phases = {"completed": "completed", "failed": "failed"}
                terminal_phase_value = terminal_phases.get(status)
                update_payload: dict = {
                    "subtasks": subtasks_data,
                    "subtasksCompleted": completed_count,
                    "subtasksTotal": len(subtasks_data),
                }
                if terminal_phase_value:
                    update_payload["phase"] = terminal_phase_value
                    update_payload["executionProgress"] = {
                        "phase": terminal_phase_value,
                        "phaseProgress": 100,
                        "overallProgress": 100,
                    }
                await self._safe_emit_task_update(task_id, update_payload)
        except Exception as e:
            logger.error("[AgentService] Failed to update plan status: %s", sanitize_log(e))
            # Still emit status event so frontend updates even if plan file write failed
            if emit_events:
                try:
                    fallback_status = phase_to_status(phase_enum) if phase_enum else status
                    fallback_reason = phase_to_review_reason(phase_enum) if phase_enum else None
                    await self._safe_emit_task_status(task_id, fallback_status, fallback_reason)
                except Exception:
                    logger.error(
                        "[AgentService] Failed to emit fallback task:status for %s",
                        sanitize_log(task_id),
                    )

    async def start_spec_creation(
        self,
        task_id: str,
        project_path: Path,
        title: str,
        description: str,
        complexity: str | None = None,
        auto_continue: bool = True,
        user_id: str = "",
    ) -> asyncio.subprocess.Process:
        """Start spec creation for a task."""
        import logging

        logger = logging.getLogger(__name__)
        if task_id in self.running_tasks:
            raise ValueError(f"Task {task_id} is already running")

        # Parse spec_id from task_id (format: "project_id:spec_id")
        if ":" in task_id:
            spec_id = safe_spec_component(task_id.split(":", 1)[1])
            spec_dir = project_path / ".pfactory" / "specs" / spec_id
        else:
            # Fallback: no project ID prefix (shouldn't happen in web mode)
            spec_dir = None

        # Fix 5: Check if task requires manual review before coding
        # If requireReviewBeforeCoding is true, DON'T auto-approve (let user review the plan)
        should_auto_approve = True  # Default for web mode
        spec_phase_model = None  # Model for spec creation phase
        if spec_dir:
            task_metadata_file = spec_dir / "task_metadata.json"
            if task_metadata_file.exists():
                try:
                    metadata = json.loads(task_metadata_file.read_text())
                    if metadata.get("requireReviewBeforeCoding", False):
                        should_auto_approve = False
                        logger.info(
                            "[AgentService] Task %s requires manual review - NOT auto-approving spec",
                            sanitize_log(task_id),
                        )
                    # Read spec phase model from auto profile config
                    if metadata.get("isAutoProfile") and metadata.get("phaseModels"):
                        spec_phase_model = metadata["phaseModels"].get("spec")
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(
                        "[AgentService] Failed to read task_metadata.json: %s", sanitize_log(e)
                    )

        # Build command
        cmd = [
            sys.executable,
            str(self.backend_path / "runners" / "spec_runner.py"),
            "--task",
            f"{title}\n\n{description}",
            "--project-dir",
            str(project_path),
        ]

        # Pass spec phase model if configured (multi-model support)
        if spec_phase_model:
            cmd.extend(["--model", spec_phase_model])
            logger.info(
                "[AgentService] [Model: %s] Starting spec creation for %s",
                sanitize_log(spec_phase_model),
                sanitize_log(task_id),
            )
        else:
            logger.info(
                "[AgentService] [Model: sonnet] Starting spec creation for %s (default)",
                sanitize_log(task_id),
            )

        # Fix 1: Only auto-approve if task doesn't require manual review
        if should_auto_approve:
            cmd.append("--auto-approve")

        # Fix 4: Pass existing spec directory to prevent duplicate task creation
        if spec_dir:
            cmd.extend(["--spec-dir", str(spec_dir)])

        if complexity:
            cmd.extend(["--complexity", complexity])

        # Set environment — scrub ANTHROPIC_API_KEY so spawned subprocesses
        # can never silently bill the direct-API account (OAuth-only policy;
        # see apps/backend/core/auth.py).
        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Run Claude in non-interactive mode - bypass permission prompts
        env["CLAUDE_CODE_ENTRYPOINT"] = "cli"  # Signal non-interactive mode
        env["CI"] = "true"  # Many CLI tools use this to detect non-interactive mode

        # Quick Mode for simple tasks (safety net if simple task reaches spec creation)
        if complexity == "simple":
            env["QUICK_MODE"] = "true"
            logger.info(
                "[AgentService] Quick Mode enabled for spec creation task %s", sanitize_log(task_id)
            )

        # Load backend .env file for graphiti and other settings
        backend_env_file = self.backend_path / ".env"
        if backend_env_file.exists():
            try:
                with open(backend_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip()
                            # Don't override existing env vars
                            if key not in env:
                                env[key] = value
                logger.info("[AgentService] Loaded backend .env for spec creation")
            except Exception as e:
                logger.warning("[AgentService] Failed to load backend .env: %s", sanitize_log(e))

        # Load project .pfactory/.env for project-level settings (USE_CLAUDE_MD, etc.)
        project_env_file = project_path / ".pfactory" / ".env"
        if project_env_file.exists():
            try:
                with open(project_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip()
                            if key not in env:
                                env[key] = value
                logger.info("[AgentService] Loaded project .env for spec creation")
            except Exception as e:
                logger.warning("[AgentService] Failed to load project .env: %s", sanitize_log(e))

        # Get OAuth token with profile tracking
        token, profile_id, profile_name = self._resolve_claude_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(
                "[AgentService] Using Claude profile for spec creation: %s (%s)",
                sanitize_log(profile_name),
                sanitize_log(profile_id),
            )
            # Store for potential retry tracking
            self._task_profiles[task_id] = {
                "profileId": profile_id,
                "profileName": profile_name,
                "attempt": 1,
                "model": spec_phase_model or "sonnet",
            }
        else:
            logger.warning("[AgentService] No Claude OAuth token available for spec creation")
            self._task_profiles[task_id] = {"attempt": 1, "model": spec_phase_model or "sonnet"}

        # Start subprocess with a pseudo-TTY to prevent "Stream closed" errors
        # Claude Code CLI expects a TTY for permission handling
        import pty

        master_fd, slave_fd = pty.openpty()

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=slave_fd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
            env=env,
        )

        # Close slave fd in parent process
        os.close(slave_fd)

        self.running_tasks[task_id] = proc

        # Initialize tracking for sequence numbers and start time
        self._task_sequence_numbers[task_id] = 0
        self._task_start_times[task_id] = datetime.now().isoformat()
        if user_id:
            self._task_user_ids[task_id] = user_id
        # Store spec directory for reading implementation plans during progress updates
        self._spec_dirs[task_id] = spec_dir

        # Emit initial progress (50% within spec_creation phase → 10% overall)
        await self._emit_progress(
            TaskProgress(
                task_id=task_id,
                phase=TaskPhase.SPEC_CREATION,
                message="Starting spec creation...",
                percentage=50,
            )
        )

        # Start output processing in background
        asyncio.create_task(self._process_output(task_id, proc.stdout, is_stderr=False))
        asyncio.create_task(self._process_output(task_id, proc.stderr, is_stderr=True))

        # Start process monitor to clean up when finished
        # Pass project_path so monitor can detect created spec and check for review state
        # Pass cmd and env so model fallback can retry with a different model on failure
        asyncio.create_task(
            self._monitor_process(task_id, proc, project_path=project_path, cmd=cmd, env=env)
        )

        return proc

    async def start_task_execution(
        self,
        task_id: str,
        project_path: Path,
        spec_id: str,
        auto_continue: bool = True,
        base_branch: str | None = None,
        mode: str | None = "full",
        force: bool = False,
        user_id: str = "",
        stop_after_planning: bool = False,
    ) -> asyncio.subprocess.Process:
        """Start task execution (run.py).

        Args:
            mode: "quick" for simplified prompts (~70% fewer tokens), "full" for comprehensive prompts.
            force: If True, bypasses approval checks (use when plan was already manually approved).
            stop_after_planning: Passes ``--stop-after-planning`` to run.py.
                Used by the Copilot delegation flow (#94) — the planner writes
                test_plan.json and run.py exits cleanly before the
                coder/QA phases.
        """
        import logging

        logger = logging.getLogger(__name__)

        # #335: barrier the caller-supplied spec_id before it becomes a path
        # segment (worktree + main spec dirs downstream). Callers pre-sanitize
        # today, so this is the module choke point CodeQL needs, not a new failure.
        spec_id = safe_spec_component(spec_id)
        if task_id in self.running_tasks:
            raise ValueError(f"Task {task_id} is already running")

        # Build command
        cmd = [
            sys.executable,
            str(self.backend_path / "run.py"),
            "--spec",
            spec_id,
            "--project-dir",
            str(project_path),
        ]

        if auto_continue:
            cmd.append("--auto-continue")

        # Check if human review before coding is required
        # If so, don't pass --force to allow the approval gate
        spec_dir = project_path / ".pfactory" / "specs" / spec_id
        requirements_file = spec_dir / "requirements.json"
        task_metadata_file = spec_dir / "task_metadata.json"
        require_review = False

        # Sync metadata from requirements.json to task_metadata.json (Bug fix)
        # Frontend writes to requirements.json, backend reads task_metadata.json
        # Ensure they stay in sync to prevent requireReviewBeforeCoding mismatches
        if requirements_file.exists():
            try:
                requirements = json.loads(requirements_file.read_text())
                frontend_metadata = requirements.get("metadata", {})

                # Read existing task_metadata or create new
                if task_metadata_file.exists():
                    task_metadata = json.loads(task_metadata_file.read_text())
                else:
                    task_metadata = {}

                # Sync requireReviewBeforeCoding from frontend to backend
                if "requireReviewBeforeCoding" in frontend_metadata:
                    task_metadata["requireReviewBeforeCoding"] = frontend_metadata[
                        "requireReviewBeforeCoding"
                    ]

                # Save updated task_metadata.json
                task_metadata_file.write_text(json.dumps(task_metadata, indent=2))

                require_review = task_metadata.get("requireReviewBeforeCoding", False)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "[AgentService] Could not sync metadata for %s: %s",
                    sanitize_log(task_id),
                    sanitize_log(e),
                )
        elif task_metadata_file.exists():
            try:
                task_metadata = json.loads(task_metadata_file.read_text())
                require_review = task_metadata.get("requireReviewBeforeCoding", False)
                # Note: Quick Mode no longer forces review - respect requireReviewBeforeCoding setting
            except (json.JSONDecodeError, OSError):
                pass

        # Write skill context file based on selectedSkills in task_metadata
        self._write_skill_context(spec_dir)

        # Add --force flag if:
        # 1. Review is not required OR
        # 2. Plan was manually approved (force=True from approve_plan endpoint)
        if not require_review or force:
            cmd.append("--force")  # Bypass approval check for headless execution
            if force:
                logger.info(
                    "[AgentService] Using --force for %s (plan manually approved)",
                    sanitize_log(task_id),
                )
        else:
            logger.info(
                "[AgentService] Human review before coding enabled for task %s - not using --force",
                sanitize_log(task_id),
            )

        if base_branch:
            cmd.extend(["--base-branch", base_branch])

        # Skip QA for quick mode (simple tasks) - coder_quick.md validates inline
        if mode == "quick":
            cmd.append("--skip-qa")
            logger.info("[AgentService] Skipping QA for quick mode task %s", sanitize_log(task_id))

        # Stop after planning for Copilot delegation flow (#94)
        if stop_after_planning:
            cmd.append("--stop-after-planning")
            logger.info(
                "[AgentService] Stop-after-planning for %s (Copilot delegation)",
                sanitize_log(task_id),
            )

        # Set environment — scrub ANTHROPIC_API_KEY so spawned subprocesses
        # can never silently bill the direct-API account (OAuth-only policy;
        # see apps/backend/core/auth.py).
        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Run Claude in non-interactive mode - bypass permission prompts
        env["CLAUDE_CODE_ENTRYPOINT"] = "cli"  # Signal non-interactive mode
        env["CI"] = "true"  # Many CLI tools use this to detect non-interactive mode

        # Quick Mode: Use simplified prompts (~70% fewer tokens)
        if mode == "quick":
            env["QUICK_MODE"] = "true"
            logger.info("[AgentService] Quick Mode enabled for task %s", sanitize_log(task_id))

        # Load backend .env file for graphiti and other settings
        backend_env_file = self.backend_path / ".env"
        if backend_env_file.exists():
            try:
                with open(backend_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip()
                            # Don't override existing env vars
                            if key not in env:
                                env[key] = value
                logger.info(
                    "[AgentService] Loaded backend .env from %s", sanitize_log(backend_env_file)
                )
            except Exception as e:
                logger.warning("[AgentService] Failed to load backend .env: %s", sanitize_log(e))

        # Load project .pfactory/.env for project-level settings (USE_CLAUDE_MD, etc.)
        project_env_file = project_path / ".pfactory" / ".env"
        if project_env_file.exists():
            try:
                with open(project_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip()
                            value = value.strip()
                            if key not in env:
                                env[key] = value
                logger.info("[AgentService] Loaded project .env for task execution")
            except Exception as e:
                logger.warning("[AgentService] Failed to load project .env: %s", sanitize_log(e))

        # Get OAuth token with profile tracking
        token, profile_id, profile_name = self._resolve_claude_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(
                "[AgentService] Using Claude profile: %s (%s)",
                sanitize_log(profile_name),
                sanitize_log(profile_id),
            )
            # Store for potential retry — read model from task_metadata.json
            exec_model = "sonnet"  # default
            exec_spec_dir = project_path / ".pfactory" / "specs" / spec_id
            exec_metadata_file = exec_spec_dir / "task_metadata.json"
            if exec_metadata_file.exists():
                try:
                    exec_metadata = json.loads(exec_metadata_file.read_text())
                    exec_model = exec_metadata.get("model", "sonnet")
                except (json.JSONDecodeError, OSError):
                    pass
            self._task_profiles[task_id] = {
                "profileId": profile_id,
                "profileName": profile_name,
                "attempt": 1,
                "model": exec_model,
            }
        else:
            logger.warning("[AgentService] No Claude OAuth token available")

        exec_model_display = self._task_profiles.get(task_id, {}).get("model", "sonnet")
        logger.info(
            "[AgentService] [Model: %s] Starting task execution for %s",
            sanitize_log(exec_model_display),
            sanitize_log(task_id),
        )
        logger.info("[AgentService] Command: %s", sanitize_log(" ".join(cmd)))

        # Claude Code Remote Control (Issue #50 / native --remote-control flag).
        # When enabled per-task, the spawned `claude` registers a session with
        # Anthropic's API that the user can drive from claude.ai/code or the
        # Claude mobile app.  Two prerequisites are tightly coupled:
        #   1. Append ``--remote-control "PFactory: <spec-id>"`` to cmd so the
        #      session is named and discoverable in the claude.ai/code session list.
        #   2. Scrub ``CLAUDE_CODE_OAUTH_TOKEN`` (and ``ANTHROPIC_AUTH_TOKEN``)
        #      from env so the subprocess falls back to ~/.claude/.credentials.json.
        #      Remote Control rejects setup-token-issued tokens with the error
        #      "Remote Control requires a full-scope login token".  The full-scope
        #      token lives in ~/.claude/.credentials.json (from ``claude auth login``)
        #      and is what core/auth.py's fallback chain reaches when env vars are
        #      absent (priority 4 in get_auth_token).
        #
        # Toggle source (in order):
        #   1. task_metadata.json :: enableRemoteControl  (per-task, frontend-set)
        #   2. project.settings.remoteControlByDefault    (per-project default)
        # Default off — Remote Control requires a paid Anthropic subscription
        # (Pro/Max/Team/Enterprise) so we can't enable it for everyone.
        _rc_enabled = False
        _rc_spec_dir = project_path / ".pfactory" / "specs" / spec_id
        _rc_metadata_file = _rc_spec_dir / "task_metadata.json"
        if _rc_metadata_file.exists():
            try:
                _rc_meta = json.loads(_rc_metadata_file.read_text())
                _rc_enabled = bool(_rc_meta.get("enableRemoteControl", False))
            except (json.JSONDecodeError, OSError):
                pass
        if not _rc_enabled:
            try:
                from ..routes.projects import load_projects

                _rc_projs = load_projects()
                _rc_pid = task_id.split(":", 1)[0]
                _rc_proj = _rc_projs.get(_rc_pid, {})
                if (_rc_proj.get("settings") or {}).get("remoteControlByDefault"):
                    _rc_enabled = True
            except Exception:
                pass

        if _rc_enabled:
            _rc_session_name = f"PFactory: {spec_id}"
            cmd.extend(["--remote-control", _rc_session_name])
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            logger.warning(
                "[AgentService] Remote Control ENABLED for task_id=%s — session %r will appear in claude.ai/code. Scrubbed CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_AUTH_TOKEN — agent will fall back to ~/.claude/.credentials.json (must be a full-scope token from `claude auth login`).",
                sanitize_log(task_id),
                sanitize_log(_rc_session_name),
            )

        # E2E test mode (Epic #44 R4): when PFACTORY_TEST_AGENT_CMD is
        # set, the agent subprocess is replaced with the override (e.g.
        # ``sleep 300``).  The rmux create hook below still fires because
        # it derives the session purely from spec_id/project_path — so the
        # Playwright suite can exercise the Live Console without burning
        # LLM tokens.  MUST NOT be set in production — bypasses the agent
        # entirely.  We log loudly when it kicks in.
        _test_cmd = os.environ.get("PFACTORY_TEST_AGENT_CMD", "").strip()
        if _test_cmd:
            import shlex

            cmd = shlex.split(_test_cmd)
            logger.warning(
                "[AgentService] PFACTORY_TEST_AGENT_CMD active — replacing agent command with %r (task_id=%s). MUST NOT be set in prod.",
                sanitize_log(cmd),
                sanitize_log(task_id),
            )

        # Start subprocess with a pseudo-TTY to prevent "Stream closed" errors
        # Claude Code CLI expects a TTY for permission handling
        import pty

        master_fd, slave_fd = pty.openpty()

        # Tee stderr to a per-spec file so failures that happen before
        # the agent writes task_logs.json are still debuggable (#146).
        # _process_output still drains the PIPE; this is an additional
        # post-mortem capture, not a replacement.
        spec_stderr_log = project_path / ".pfactory" / "specs" / spec_id / "spawn_stderr.log"
        try:
            spec_stderr_log.parent.mkdir(parents=True, exist_ok=True)
            spec_stderr_log.write_text("")  # truncate any previous capture
        except OSError as _e:
            logger.debug("[AgentService] could not prep spawn_stderr.log: %s", sanitize_log(_e))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=slave_fd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
            env=env,
        )

        # Close slave fd in parent process
        os.close(slave_fd)

        # Track the per-spec stderr file so _process_output can mirror
        # stderr lines into it.
        self._spec_stderr_logs[task_id] = spec_stderr_log

        self.running_tasks[task_id] = proc

        # Initialize tracking for sequence numbers and start time
        self._task_sequence_numbers[task_id] = 0
        self._task_start_times[task_id] = datetime.now().isoformat()
        # Store spec directory for reading implementation plans during progress updates
        self._spec_dirs[task_id] = spec_dir

        # Create TaskLogWriter for detailed phase logs
        # Write to worktree spec dir (will be synced to main spec dir)
        worktree_spec_dir = (
            project_path
            / ".pfactory"
            / "worktrees"
            / "tasks"
            / spec_id
            / ".pfactory"
            / "specs"
            / spec_id
        )
        worktree_spec_dir.mkdir(parents=True, exist_ok=True)
        log_writer = TaskLogWriter(worktree_spec_dir)

        # Also write to main spec dir for immediate visibility
        main_spec_dir = project_path / ".pfactory" / "specs" / spec_id
        main_spec_dir.mkdir(parents=True, exist_ok=True)
        main_log_writer = TaskLogWriter(main_spec_dir)

        # Store log writers for cleanup
        self._task_log_writers[task_id] = (log_writer, main_log_writer)

        # Emit initial progress (100% within planning phase → 20% overall)
        await self._emit_progress(
            TaskProgress(
                task_id=task_id,
                phase=TaskPhase.PLANNING,
                message="Starting task execution...",
                percentage=100,
            )
        )

        # Initialize planning phase in logs
        log_writer.set_phase_status(spec_id, TaskPhase.PLANNING, "active")
        main_log_writer.set_phase_status(spec_id, TaskPhase.PLANNING, "active")

        # Start output processing in background with log writers
        asyncio.create_task(
            self._process_output(
                task_id, proc.stdout, is_stderr=False, log_writer=log_writer, spec_id=spec_id
            )
        )
        asyncio.create_task(self._process_output(task_id, proc.stderr, is_stderr=True))

        # Start process monitor to clean up when finished (with file syncing and failover support)
        asyncio.create_task(self._monitor_process(task_id, proc, project_path, spec_id, cmd, env))

        # Epic #44 R1 — opt-in Live Agent Console. No-op when
        # PFACTORY_RMUX_ENABLED is unset/false (the default), so the
        # bank-pilot image's behaviour is byte-for-byte unchanged.
        from ..rmux.integration import create_if_enabled as _rmux_create

        try:
            await _rmux_create(spec_id, project_path, " ".join(cmd))
        except Exception:
            # Already swallowed inside _rmux_create; this except is a
            # belt-and-suspenders guard so a wrapper bug here cannot
            # take down task execution.
            logger.warning(
                "[AgentService] rmux create hook raised (ignored); spec_id=%s",
                sanitize_log(spec_id),
            )

        return proc

    async def stop_task(self, task_id: str) -> bool:
        """Stop a running task."""
        import logging

        logger = logging.getLogger(__name__)
        if task_id not in self.running_tasks:
            logger.info(
                "[AgentService] Task %s not in running_tasks (already stopped or never started)",
                sanitize_log(task_id),
            )
            return False

        # Mark as stopped BEFORE termination so _monitor_process defers to us
        self._task_stopped.add(task_id)

        proc = self.running_tasks[task_id]
        proc.terminate()

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()

        # Get actual phase and spec info BEFORE cleanup
        actual_phase = self._get_current_phase(task_id)
        spec_dir = self._spec_dirs.get(task_id)

        # Finalize log writers — flush pending text, mark phase as failed
        if task_id in self._task_log_writers:
            log_writer, main_log_writer = self._task_log_writers[task_id]
            # Parse spec_id from task_id (format: "project_id:spec_id")
            spec_id = safe_spec_component(task_id.split(":", 1)[1] if ":" in task_id else task_id)
            log_writer.finalize(spec_id, actual_phase)
            log_writer.set_phase_status(spec_id, actual_phase, "failed")
            main_log_writer.finalize(spec_id, actual_phase)
            main_log_writer.set_phase_status(spec_id, actual_phase, "failed")
            del self._task_log_writers[task_id]
            logger.debug(
                "[AgentService] Finalized task logs for stopped task %s", sanitize_log(task_id)
            )

        # Persist failed status to test_plan.json
        if spec_dir:
            # Derive project_path: spec_dir is .pfactory/specs/XXX, project root is 3 levels up
            project_path = spec_dir.parent.parent.parent
            spec_id = safe_spec_component(task_id.split(":", 1)[1] if ":" in task_id else task_id)
            await self._update_plan_status(project_path, spec_id, "failed", task_id)

        # Epic #44 R1 — reap rmux session if the feature was on. Idempotent
        # so safe even though _monitor_process may also reap on the natural
        # exit path.
        from ..rmux.integration import reap_if_enabled as _rmux_reap

        _reap_spec_id = safe_spec_component(task_id.split(":", 1)[1] if ":" in task_id else task_id)
        try:
            await _rmux_reap(_reap_spec_id)
        except Exception:
            logger.warning(
                "[AgentService] rmux reap hook raised in stop_task (ignored); spec_id=%s",
                sanitize_log(_reap_spec_id),
            )

        # Use pop with default to handle race condition where _monitor_process
        # might have already removed the task
        self.running_tasks.pop(task_id, None)
        self._task_sequence_numbers.pop(task_id, None)
        self._last_emitted_task_update.pop(task_id, None)
        self._task_start_times.pop(task_id, None)
        self._task_subtask_states.pop(task_id, None)
        self._spec_dirs.pop(task_id, None)
        self._task_current_phases.pop(task_id, None)
        self._task_profiles.pop(task_id, None)
        self._task_rate_limits.pop(task_id, None)
        self._task_user_ids.pop(task_id, None)

        # Emit human_review with errors reason (not just FAILED phase)
        await self._safe_emit_task_status(task_id, "human_review", "errors")
        await self._emit_progress(
            TaskProgress(
                task_id=task_id,
                phase=TaskPhase.FAILED,
                message="Task stopped by user",
            )
        )

        return True

    async def wait_for_task(self, task_id: str) -> int:
        """Wait for a task to complete and return exit code."""
        if task_id not in self.running_tasks:
            raise ValueError(f"Task {task_id} is not running")

        proc = self.running_tasks[task_id]
        return_code = await proc.wait()

        del self.running_tasks[task_id]
        self._task_sequence_numbers.pop(task_id, None)
        self._last_emitted_task_update.pop(task_id, None)
        self._task_start_times.pop(task_id, None)
        self._task_subtask_states.pop(task_id, None)
        self._spec_dirs.pop(task_id, None)

        if return_code == 0:
            await self._emit_progress(
                TaskProgress(
                    task_id=task_id,
                    phase=TaskPhase.COMPLETED,
                    message="Task completed successfully",
                )
            )
        else:
            await self._emit_progress(
                TaskProgress(
                    task_id=task_id,
                    phase=TaskPhase.FAILED,
                    message=f"Task failed with exit code {return_code}",
                )
            )

        return return_code

    def is_running(self, task_id: str) -> bool:
        """Check if a task is currently running."""
        return task_id in self.running_tasks

    def get_running_tasks(self) -> list[str]:
        """Get list of running task IDs."""
        return list(self.running_tasks.keys())


# Global service instance
_agent_service: AgentService | None = None


def get_agent_service() -> AgentService:
    """Get the global agent service instance."""
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service
