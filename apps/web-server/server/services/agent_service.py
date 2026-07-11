"""
Agent execution service.

Wraps the existing run.py and spec_runner.py CLI tools as async services,
enabling task execution with real-time streaming of logs and progress.
"""

import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..config import get_settings
from ..utils.subprocess_env import make_subprocess_env
from ..websockets.events import (
    emit_task_status,
    emit_task_update,
)

# Re-export the task-model cluster so all existing import paths keep working
# unchanged. The ``X as X`` form is the mypy-standard explicit re-export idiom
# (satisfies no_implicit_reexport); PLC0414 is suppressed per-line because the
# rule fires on non-renaming aliases but the pattern IS intentional here.
from .task_models import PHASE_RANGES as PHASE_RANGES  # noqa: PLC0414
from .task_models import TaskLog as TaskLog  # noqa: PLC0414
from .task_models import TaskPhase as TaskPhase  # noqa: PLC0414
from .task_models import TaskProgress as TaskProgress  # noqa: PLC0414
from .task_models import _dedup_signature as _dedup_signature  # noqa: PLC0414
from .task_models import phase_to_review_reason as phase_to_review_reason  # noqa: PLC0414
from .task_models import phase_to_status as phase_to_status  # noqa: PLC0414
from .task_models import scale_progress as scale_progress  # noqa: PLC0414

# TaskLogWriter moved to task_log_writer.py (Factory#255 seam b); re-exported
# here so existing import paths keep working unchanged.
from .task_log_writer import TaskLogWriter as TaskLogWriter  # noqa: PLC0414
from .agent_failover import AgentFailoverMixin
from .agent_worktree_sync import AgentWorktreeSyncMixin


class AgentService(AgentFailoverMixin, AgentWorktreeSyncMixin):
    """Service for executing AI agents on tasks."""

    def __init__(self):
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
            _logger.debug("[AgentService] dedup-suppressed task:update for %s", task_id)
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

    async def _emit_progress(self, progress: TaskProgress, previous_phase: TaskPhase | None = None) -> None:
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
                                subtasks_data.append({
                                    "id": subtask.get("id", ""),
                                    "status": subtask.get("status", "pending"),
                                    "title": subtask.get("description", ""),
                                })
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug(f"[AgentService] Could not read subtasks for {progress.task_id}: {e}")

            await self._safe_emit_task_update(progress.task_id, {
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
            })

            # If phase changed, also emit status change for kanban column movement
            if previous_phase is not None and progress.phase != previous_phase:
                new_status = phase_to_status(progress.phase)
                review_reason = phase_to_review_reason(progress.phase)
                await self._safe_emit_task_status(progress.task_id, new_status, review_reason)

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[AgentService] WebSocket broadcast failed: {e}")

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

    def _parse_phase_event(self, line: str) -> dict | None:
        """Parse phase event from agent output.

        Supports two formats:
        1. [PHASE_EVENT] phase=coding message="Starting"
        2. __EXEC_PHASE__:{"phase":"coding","message":"Starting","progress":50}
        """
        # Check for __EXEC_PHASE__: prefix (JSON format from backend)
        exec_phase_prefix = "__EXEC_PHASE__:"
        if line.startswith(exec_phase_prefix):
            try:
                json_str = line[len(exec_phase_prefix):]
                event = json.loads(json_str)
                # Map 'progress' to 'percentage' for consistency
                if "progress" in event:
                    event["percentage"] = event.pop("progress")
                return event
            except json.JSONDecodeError:
                return None

        # Check for [PHASE_EVENT] prefix (key=value format)
        match = re.match(r"\[PHASE_EVENT\]\s*(.+)", line)
        if not match:
            return None

        event_str = match.group(1)
        event = {}

        # Parse key=value pairs
        for part in re.findall(r"(\w+)=([^\s]+|\"[^\"]+\")", event_str):
            key, value = part
            value = value.strip('"')
            event[key] = value

        return event if event else None

    async def _process_output(
        self,
        task_id: str,
        stream: asyncio.StreamReader,
        is_stderr: bool = False,
        log_writer: TaskLogWriter | None = None,
        spec_id: str | None = None,
    ) -> TaskPhase:
        """Process output stream from subprocess.

        Returns the final phase detected.
        """
        import logging
        logger = logging.getLogger(__name__)
        # Use the tracked phase if available (e.g., PLANNING when started via start_task_execution),
        # otherwise default to SPEC_CREATION for spec creation processes
        current_phase = self._task_current_phases.get(task_id, TaskPhase.SPEC_CREATION)

        async for line_bytes in stream:
            line = line_bytes.decode("utf-8", errors="replace").rstrip()

            # Log stderr to server logs for debugging
            if is_stderr and line:
                logger.warning(f"[AgentService] Task {task_id} stderr: {line}")
                # Also mirror stderr to a per-spec file so post-mortem
                # debugging works even when the subprocess dies before
                # writing its own task_logs.json (#146).
                stderr_file = self._spec_stderr_logs.get(task_id)
                if stderr_file is not None:
                    try:
                        with stderr_file.open("a", encoding="utf-8") as fh:
                            fh.write(line + "\n")
                    except OSError:
                        pass

            # Create log entry
            log = TaskLog(
                task_id=task_id,
                content=line,
                source="stderr" if is_stderr else "stdout",
                level="error" if is_stderr else "info",
            )
            await self._emit_log(log)

            # Detect rate limit messages to trigger failover after exit
            if self._is_rate_limit_line(line):
                self._task_rate_limits[task_id] = True
                logger.warning(f"[AgentService] Rate limit detected for task {task_id} (will attempt failover if enabled)")

            # Write to task_logs.json for detailed phase logs
            if log_writer and spec_id and not is_stderr:
                log_writer.process_line(spec_id, current_phase, line)

            # Check for phase events (__EXEC_PHASE__: or [PHASE_EVENT])
            event = self._parse_phase_event(line)
            if event:
                phase_str = event.get("phase", "")
                phase_map = {
                    "spec_creation": TaskPhase.SPEC_CREATION,
                    "planning": TaskPhase.PLANNING,
                    "coding": TaskPhase.CODING,
                    "qa_review": TaskPhase.QA_REVIEW,
                    "qa_fixing": TaskPhase.QA_FIXING,
                    "complete": TaskPhase.COMPLETED,  # backend uses "complete"
                    "completed": TaskPhase.COMPLETED,
                    "failed": TaskPhase.FAILED,
                }
                old_phase = current_phase
                if phase_str in phase_map:
                    current_phase = phase_map[phase_str]

                    # Track current phase for proper status on task completion
                    self._task_current_phases[task_id] = current_phase

                    # Update log writer phase status
                    if log_writer and spec_id:
                        if old_phase != current_phase:
                            log_writer.set_phase_status(spec_id, old_phase, "completed")
                        # For COMPLETED/FAILED phases, don't set them as "active" - just mark previous complete
                        if current_phase not in (TaskPhase.COMPLETED, TaskPhase.FAILED):
                            log_writer.set_phase_status(spec_id, current_phase, "active")
                        # Ensure validation phase is properly marked completed when task completes
                        if current_phase == TaskPhase.COMPLETED and old_phase in (TaskPhase.QA_REVIEW, TaskPhase.QA_FIXING):
                            log_writer.set_phase_status(spec_id, old_phase, "completed")

                # Always emit progress for phase events (even if phase didn't change)
                progress = TaskProgress(
                    task_id=task_id,
                    phase=current_phase,
                    message=event.get("message", ""),
                    subtask=event.get("subtask"),
                    subtask_index=int(event["subtask_index"]) if "subtask_index" in event else None,
                    subtask_total=int(event["subtask_total"]) if "subtask_total" in event else None,
                    percentage=event.get("percentage"),  # Include percentage from event
                    data=event,
                )
                # Pass previous phase if it changed, so status event can be emitted
                await self._emit_progress(progress, previous_phase=old_phase if old_phase != current_phase else None)

            # Check for JSON progress data
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    if "phase" in data or "status" in data:
                        phase_str = data.get("phase", data.get("status", ""))
                        if phase_str in ["coding", "planning", "qa_review", "qa_fixing"]:
                            old_phase = current_phase
                            current_phase = TaskPhase(phase_str)

                            # Track current phase for proper status on task completion
                            self._task_current_phases[task_id] = current_phase

                            # Update log writer phase status
                            if log_writer and spec_id:
                                if old_phase != current_phase:
                                    log_writer.set_phase_status(spec_id, old_phase, "completed")
                                log_writer.set_phase_status(spec_id, current_phase, "active")

                        progress = TaskProgress(
                            task_id=task_id,
                            phase=current_phase,
                            message=data.get("message", ""),
                            subtask=data.get("subtask"),
                            subtask_index=data.get("subtask_index"),
                            subtask_total=data.get("subtask_total"),
                            percentage=data.get("percentage"),
                            data=data,
                        )
                        # Pass previous phase if it changed, so status event can be emitted
                        await self._emit_progress(progress, previous_phase=old_phase if old_phase != current_phase else None)
                except json.JSONDecodeError:
                    pass

        return current_phase

    async def _monitor_process(
        self,
        task_id: str,
        proc: asyncio.subprocess.Process,
        project_path: Path | None = None,
        spec_id: str | None = None,
        cmd: list[str] | None = None,
        env: dict | None = None
    ) -> None:
        """Monitor subprocess and clean up when it finishes.

        Also periodically syncs files from worktree to main spec dir if project_path and spec_id are provided.
        Supports profile failover on early failures when cmd and env are provided.
        """
        import logging
        logger = logging.getLogger(__name__)

        try:
            # Periodic sync loop (every 3 seconds) while process is running
            sync_interval = 3.0

            rate_limit_forced_restart = False
            return_code: int | None = None

            while True:
                # Check if process has finished
                try:
                    return_code = await asyncio.wait_for(proc.wait(), timeout=sync_interval)
                    # Process finished
                    break
                except asyncio.TimeoutError:
                    # Process still running, sync files
                    if project_path and spec_id:
                        await self._sync_worktree_files(project_path, spec_id, task_id)

                    # Fix Bug #3: For spec creation, check if review checkpoint reached while process is running
                    if project_path and not spec_id:
                        # Detect if spec_runner created plan_review.html (review checkpoint reached)
                        # Parse spec_id from task_id (format: "project_id:spec_id")
                        detected_spec_id = None
                        if ":" in task_id:
                            _, detected_spec_id = task_id.split(":", 1)

                        if detected_spec_id:
                            detected_spec_dir = project_path / ".pfactory" / "specs" / detected_spec_id
                            plan_review_file = detected_spec_dir / "plan_review.html"

                            # Check if plan_review.html exists (indicates review checkpoint reached)
                            if plan_review_file.exists():
                                # Check if we've already emitted PLAN_REVIEW for this task
                                current_phase = self._task_current_phases.get(task_id)
                                if current_phase != TaskPhase.PLAN_REVIEW:
                                    logger.info(f"[AgentService] Detected review checkpoint for {detected_spec_id} (plan_review.html exists)")

                                    # Update plan status to human_review
                                    await self._update_plan_status(project_path, detected_spec_id, "human_review", task_id)

                                    # Emit PLAN_REVIEW phase (maps to "human_review" status) — plan_review always scales to 20%
                                    await self._emit_progress(
                                        TaskProgress(
                                            task_id=task_id,
                                            phase=TaskPhase.PLAN_REVIEW,
                                            message="Spec created - waiting for human approval",
                                            percentage=100,
                                        ),
                                        previous_phase=TaskPhase.SPEC_CREATION,  # Enable status event emission
                                    )

                                    # Mark phase as emitted
                                    self._task_current_phases[task_id] = TaskPhase.PLAN_REVIEW
                                    logger.info(f"[AgentService] Emitted PLAN_REVIEW status for {task_id}")

                    # If we detect a rate limit and failover is enabled, don't wait for the process to exit.
                    if cmd and env:
                        profile_info = self._task_profiles.get(task_id, {})
                        attempt = profile_info.get("attempt", 1)
                        rate_limit_detected = self._task_rate_limits.get(task_id, False)

                        if (
                            rate_limit_detected
                            and attempt == 1
                            and self._should_retry_with_failover()
                        ):
                            logger.warning(
                                f"[AgentService] Rate limit detected for {task_id} while running; terminating process to trigger profile failover"
                            )
                            rate_limit_forced_restart = True
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            try:
                                return_code = await proc.wait()
                            except Exception:
                                return_code = 1
                            break

            if return_code is None:
                return_code = 1
            if rate_limit_forced_restart and return_code == 0:
                # Ensure we trigger the retry path.
                return_code = 1

            # Process exited - do final sync
            if project_path and spec_id:
                await self._sync_worktree_files(project_path, spec_id, task_id)

            exit_model = self._task_profiles.get(task_id, {}).get("model", "unknown")
            logger.info(f"[AgentService] [Model: {exit_model}] Task {task_id} process exited with code {return_code}")

            # Early model fallback: if a non-Claude model failed, retry with Sonnet
            # before any other processing (spec detection, plan status, etc.)
            if return_code != 0 and cmd and env:
                _fb_info = self._task_profiles.get(task_id, {})
                _fb_model = _fb_info.get("model", "")
                _fb_attempt = _fb_info.get("attempt", 1)
                _fb_is_non_claude = (
                    _fb_model
                    and not _fb_model.startswith("claude-")
                    and _fb_model not in ("haiku", "sonnet", "opus", "opus-1m")
                )
                logger.info(f"[AgentService] Fallback check: model={_fb_model!r}, attempt={_fb_attempt}, is_non_claude={_fb_is_non_claude}, cmd={'yes' if cmd else 'no'}, env={'yes' if env else 'no'}")
                if _fb_is_non_claude and _fb_attempt <= 1:
                    new_proc = await self._retry_task_with_fallback_model(
                        task_id, project_path, spec_id, cmd, env
                    )
                    if new_proc:
                        self._task_rate_limits.pop(task_id, None)
                        self.running_tasks[task_id] = new_proc

                        log_writer = None
                        main_log_writer = None
                        if task_id in self._task_log_writers:
                            log_writer, main_log_writer = self._task_log_writers[task_id]

                        asyncio.create_task(
                            self._process_output(
                                task_id, new_proc.stdout, is_stderr=False,
                                log_writer=log_writer, spec_id=spec_id,
                            )
                        )
                        asyncio.create_task(
                            self._process_output(
                                task_id, new_proc.stderr, is_stderr=True,
                                log_writer=log_writer, spec_id=spec_id,
                            )
                        )
                        asyncio.create_task(
                            self._monitor_process(
                                task_id, new_proc, project_path, spec_id,
                                cmd=None, env=None
                            )
                        )
                        logger.info(f"[AgentService] Task {task_id} restarted with fallback model (sonnet)")
                        return

            # Special case: Spec creation (project_path provided, spec_id is None)
            # Need to detect the created spec_id and check if it requires review
            if project_path and not spec_id:
                logger.info("[AgentService] Spec creation completed, detecting created spec...")
                try:
                    specs_dir = project_path / ".pfactory" / "specs"
                    if specs_dir.exists():
                        # Find the newest spec directory (just created)
                        spec_dirs = sorted(
                            [d for d in specs_dir.iterdir() if d.is_dir()],
                            key=lambda d: d.stat().st_mtime,
                            reverse=True
                        )
                        if spec_dirs:
                            detected_spec_dir = spec_dirs[0]
                            detected_spec_id = detected_spec_dir.name
                            logger.info(f"[AgentService] Detected created spec: {detected_spec_id}")

                            # Check if this spec requires review
                            review_state_file = detected_spec_dir / "review_state.json"
                            if review_state_file.exists():
                                review_data = json.loads(review_state_file.read_text())
                                if not review_data.get("approved", False):
                                    # Spec creation completed, now waiting for review
                                    logger.info(f"[AgentService] Spec {detected_spec_id} requires human review")

                                    # Update plan status to human_review
                                    await self._update_plan_status(project_path, detected_spec_id, "human_review", task_id)

                                    # Clean up tracking data
                                    if task_id in self.running_tasks:
                                        del self.running_tasks[task_id]
                                    self._task_sequence_numbers.pop(task_id, None)
                                    self._last_emitted_task_update.pop(task_id, None)
                                    self._task_start_times.pop(task_id, None)
                                    self._task_current_phases.pop(task_id, None)
                                    self._task_profiles.pop(task_id, None)
                                    self._task_subtask_states.pop(task_id, None)

                                    # Emit PLAN_REVIEW phase (maps to "human_review" status) — plan_review always scales to 20%
                                    await self._emit_progress(
                                        TaskProgress(
                                            task_id=task_id,
                                            phase=TaskPhase.PLAN_REVIEW,
                                            message="Spec created - waiting for human approval",
                                            percentage=100,
                                        ),
                                        previous_phase=TaskPhase.SPEC_CREATION,  # Enable status event emission
                                    )

                                    logger.info(f"[AgentService] Spec {detected_spec_id} transitioned to PLAN_REVIEW phase")
                                    return  # Exit early - not a failure

                            # If we reach here, spec was created but doesn't need review
                            # Auto-start task execution immediately
                            logger.info(f"[AgentService] Spec {detected_spec_id} created successfully (no review required) — auto-starting execution")

                            # Clean up tracking data from spec creation
                            if task_id in self.running_tasks:
                                del self.running_tasks[task_id]
                            self._task_sequence_numbers.pop(task_id, None)
                            self._last_emitted_task_update.pop(task_id, None)
                            self._task_start_times.pop(task_id, None)
                            self._task_current_phases.pop(task_id, None)
                            self._task_profiles.pop(task_id, None)
                            self._task_rate_limits.pop(task_id, None)
                            self._task_subtask_states.pop(task_id, None)

                            # Auto-start task execution
                            try:
                                await self.start_task_execution(
                                    task_id=task_id,
                                    project_path=project_path,
                                    spec_id=detected_spec_id,
                                    auto_continue=True,
                                )
                                logger.info(f"[AgentService] Task execution auto-started for {detected_spec_id}")
                            except Exception as exec_err:
                                logger.error(f"[AgentService] Failed to auto-start execution for {detected_spec_id}: {exec_err}")
                                # Fall back to human_review status so user can start manually
                                await self._update_plan_status(project_path, detected_spec_id, "completed", task_id)
                            return  # Exit early
                except Exception as e:
                    logger.warning(f"[AgentService] Failed to detect created spec: {e}")
                    # Fall through to normal completion handling

            # Check if task is waiting for review (can exit with code 0 or 1)
            # Code 0: auto_continue mode (web UI) - exits cleanly after saving review state
            # Code 1: CLI mode - exits with error when blocked (legacy behavior)
            if project_path and spec_id:
                spec_dir = project_path / ".pfactory" / "specs" / spec_id
                review_state_file = spec_dir / "review_state.json"

                # If review_state.json exists with approved=false, task is waiting for human review
                if review_state_file.exists():
                    try:
                        review_data = json.loads(review_state_file.read_text())
                        if not review_data.get("approved", False):
                            # This is NOT a failure - it's waiting for human review!
                            logger.info(f"[AgentService] Task {task_id} awaiting human review (not a failure)")

                            # Get actual phase BEFORE cleanup
                            actual_phase = self._get_current_phase(task_id)

                            # Finalize log writers for the phase we were in
                            if task_id in self._task_log_writers:
                                log_writer, main_log_writer = self._task_log_writers[task_id]
                                if spec_id:
                                    log_writer.finalize(spec_id, actual_phase)
                                    log_writer.set_phase_status(spec_id, actual_phase, "completed")
                                    main_log_writer.finalize(spec_id, actual_phase)
                                    main_log_writer.set_phase_status(spec_id, actual_phase, "completed")
                                del self._task_log_writers[task_id]

                            # Update plan status to human_review
                            await self._update_plan_status(project_path, spec_id, "human_review", task_id)

                            # Clean up tracking data
                            if task_id in self.running_tasks:
                                del self.running_tasks[task_id]
                            self._task_sequence_numbers.pop(task_id, None)
                            self._last_emitted_task_update.pop(task_id, None)
                            self._task_start_times.pop(task_id, None)
                            self._task_current_phases.pop(task_id, None)
                            self._task_profiles.pop(task_id, None)
                            self._task_subtask_states.pop(task_id, None)
                            self._spec_dirs.pop(task_id, None)

                            # Determine emit phase based on what phase the task was actually in
                            # If task was coding/QA, it finished implementation → show 100% progress
                            # If task was still planning, it just finished planning → show 20% progress
                            if actual_phase in (TaskPhase.CODING, TaskPhase.QA_REVIEW, TaskPhase.QA_FIXING, TaskPhase.COMPLETED):
                                emit_phase = TaskPhase.COMPLETED
                                emit_message = "Task completed - waiting for human review"
                                emit_overall = 100
                            else:
                                emit_phase = TaskPhase.PLAN_REVIEW
                                emit_message = "Plan created - waiting for human approval"
                                emit_overall = None  # Let scale_progress handle it (20%)

                            await self._emit_progress(
                                TaskProgress(
                                    task_id=task_id,
                                    phase=emit_phase,
                                    message=emit_message,
                                    percentage=100,
                                    overall_progress=emit_overall,
                                ),
                                previous_phase=actual_phase,  # Enable status event emission
                            )

                            logger.info(f"[AgentService] Task {task_id} transitioned to {emit_phase.value} phase (was {actual_phase.value})")
                            return  # Exit early - not a failure

                    except (json.JSONDecodeError, OSError) as e:
                        logger.debug(f"[AgentService] Could not read review_state.json: {e}")
                        # Fall through to treat as actual failure

            # Check for early failure and attempt profile failover
            if return_code != 0 and project_path and spec_id and cmd and env:
                spec_dir = project_path / ".pfactory" / "specs" / spec_id

                # Check if this is an early failure (no logs written)
                is_early = self._is_early_failure(spec_dir, return_code)
                rate_limit_detected = self._task_rate_limits.get(task_id, False)

                # Check if we should retry (settings enabled + first attempt)
                profile_info = self._task_profiles.get(task_id, {})
                attempt = profile_info.get("attempt", 1)
                should_retry = (
                    (is_early or rate_limit_detected)
                    and attempt == 1  # Only retry once
                    and self._should_retry_with_failover()
                )

                if should_retry:
                    failed_profile_id = profile_info.get("profileId")
                    reason = "rate_limit" if rate_limit_detected else "early_failure"
                    logger.info(f"[AgentService] {reason.replace('_', ' ')} detected for {task_id}, attempting profile failover")

                    # Attempt retry with different profile
                    if not failed_profile_id:
                        logger.warning(f"[AgentService] No failed profile recorded for {task_id}; cannot failover")
                        new_proc = None
                    else:
                        new_proc = await self._retry_task_with_profile(
                            task_id, project_path, spec_id, cmd, env, failed_profile_id, reason
                        )

                    if new_proc:
                        # Clear the flag for the new attempt so it can detect rate limits again.
                        self._task_rate_limits.pop(task_id, None)

                        # Update running task reference
                        self.running_tasks[task_id] = new_proc

                        # Get log writers for output processing
                        log_writer = None
                        main_log_writer = None
                        if task_id in self._task_log_writers:
                            log_writer, main_log_writer = self._task_log_writers[task_id]

                        # Restart output processing for new subprocess
                        asyncio.create_task(
                            self._process_output(
                                task_id,
                                new_proc.stdout,
                                is_stderr=False,
                                log_writer=log_writer,
                                spec_id=spec_id,
                            )
                        )
                        asyncio.create_task(
                            self._process_output(
                                task_id,
                                new_proc.stderr,
                                is_stderr=True,
                                log_writer=log_writer,
                                spec_id=spec_id,
                            )
                        )

                        # Restart monitoring for new subprocess (without cmd/env to prevent infinite retry)
                        asyncio.create_task(
                            self._monitor_process(
                                task_id,
                                new_proc,
                                project_path,
                                spec_id,
                                cmd=None,  # Prevent second retry
                                env=None   # Prevent second retry
                            )
                        )

                        logger.info(f"[AgentService] Task {task_id} restarted with alternate profile")
                        return  # Exit this monitor instance
                    else:
                        logger.warning(f"[AgentService] No alternate profile available for task {task_id}, trying model fallback")


            # If stop_task() already handled cleanup, skip duplicate processing
            if task_id in self._task_stopped:
                self._task_stopped.discard(task_id)
                logger.info(f"[AgentService] Task {task_id} was stopped by user, skipping _monitor_process cleanup")
                return

            # Get actual phase BEFORE cleanup (needed for proper status emission)
            actual_phase = self._get_current_phase(task_id)
            final_status = "completed" if return_code == 0 else "failed"

            # Finalize and clean up log writers
            if task_id in self._task_log_writers:
                log_writer, main_log_writer = self._task_log_writers[task_id]

                # Finalize both log writers - set status on the phase the task was actually in
                if spec_id:
                    log_writer.finalize(spec_id, actual_phase)
                    log_writer.set_phase_status(spec_id, actual_phase, final_status)
                    main_log_writer.finalize(spec_id, actual_phase)
                    main_log_writer.set_phase_status(spec_id, actual_phase, final_status)

                del self._task_log_writers[task_id]
                logger.debug(f"[AgentService] Finalized task logs for {task_id}")

            # Auto-continuation: if process exited successfully but subtasks remain,
            # restart execution instead of marking as completed (max 10 continuation rounds)
            if return_code == 0 and spec_id and project_path and cmd and env:
                plan_file = project_path / ".pfactory" / "specs" / spec_id / "test_plan.json"
                if plan_file.exists():
                    try:
                        plan_data = json.loads(plan_file.read_text())
                        pending_count = 0
                        completed_count = 0
                        total_count = 0
                        for phase in plan_data.get("phases", []):
                            for subtask in phase.get("subtasks", []):
                                total_count += 1
                                st = subtask.get("status", "pending")
                                if st in ("pending", "in_progress"):
                                    pending_count += 1
                                elif st == "completed":
                                    completed_count += 1

                        # Track continuation rounds to prevent infinite loops
                        continuation_key = f"_continuation_{task_id}"
                        round_num = getattr(self, continuation_key, 0) + 1

                        if pending_count > 0 and round_num <= 10:
                            setattr(self, continuation_key, round_num)
                            logger.info(
                                f"[AgentService] Auto-continuation round {round_num}: "
                                f"{completed_count}/{total_count} subtasks done, "
                                f"{pending_count} remaining for {spec_id}"
                            )

                            # Clean up current run tracking
                            if task_id in self.running_tasks:
                                del self.running_tasks[task_id]
                            self._task_sequence_numbers.pop(task_id, None)
                            self._last_emitted_task_update.pop(task_id, None)
                            self._task_start_times.pop(task_id, None)
                            self._task_current_phases.pop(task_id, None)
                            self._task_profiles.pop(task_id, None)
                            self._task_rate_limits.pop(task_id, None)
                            self._task_subtask_states.pop(task_id, None)
                            if task_id in self._task_log_writers:
                                log_writer, main_log_writer = self._task_log_writers[task_id]
                                if spec_id:
                                    actual_phase_for_logs = self._get_current_phase(task_id)
                                    log_writer.finalize(spec_id, actual_phase_for_logs)
                                    main_log_writer.finalize(spec_id, actual_phase_for_logs)
                                del self._task_log_writers[task_id]

                            # Restart execution
                            try:
                                await self.start_task_execution(
                                    task_id=task_id,
                                    project_path=project_path,
                                    spec_id=spec_id,
                                    auto_continue=True,
                                )
                                logger.info(f"[AgentService] Auto-continuation started for {spec_id} (round {round_num})")
                                return  # Exit this monitor — new monitor will take over
                            except Exception as e:
                                logger.error(f"[AgentService] Auto-continuation failed for {spec_id}: {e}")
                                # Fall through to normal completion
                        elif pending_count > 0 and round_num > 10:
                            logger.warning(
                                f"[AgentService] Auto-continuation limit reached (10 rounds) for {spec_id}, "
                                f"{pending_count} subtasks still pending"
                            )
                        else:
                            # All subtasks done — clean up continuation tracker
                            if hasattr(self, continuation_key):
                                delattr(self, continuation_key)
                            logger.info(f"[AgentService] All {total_count} subtasks completed for {spec_id}")
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning(f"[AgentService] Could not check subtask status for auto-continuation: {e}")

            # Update test_plan.json status for frontend display.
            # emit_events=False (Issue #14): the subsequent _emit_progress
            # call at lines ~1830/1856 is the SINGLE canonical terminal
            # emission. Letting _update_plan_status also emit produced the
            # 5-event flurry + phase:N/A blip — kept the file write here,
            # moved the WebSocket events to the explicit _emit_progress.
            if spec_id and project_path:
                status = "completed" if return_code == 0 else "failed"
                logger.info(f"[AgentService._monitor_process] About to call _update_plan_status: spec_id={spec_id}, status={status}, task_id={task_id}, project_path={project_path}")
                await self._update_plan_status(
                    project_path, spec_id, status, task_id, emit_events=False
                )
                logger.info("[AgentService._monitor_process] _update_plan_status call completed")

            # Send email/in-app notifications on task completion or failure
            _notif_user_id = self._task_user_ids.pop(task_id, "")

            # Emit completion/failure progress with previous_phase to trigger status event
            # NOTE: Cleanup is deferred until AFTER these emissions so _emit_progress
            # can still read _spec_dirs (for plan file), _task_sequence_numbers, and _task_start_times
            if return_code == 0:
                await self._emit_progress(
                    TaskProgress(
                        task_id=task_id,
                        phase=TaskPhase.COMPLETED,
                        message="Task completed successfully",
                        percentage=100,
                        overall_progress=100,
                    ),
                    previous_phase=actual_phase,  # Enable status event emission
                )
                if _notif_user_id:
                    try:
                        from .notification_service import notification_service
                        _proj_name = project_path.name if project_path else ""
                        _proj_id = task_id.split(":")[0] if ":" in task_id else ""
                        await notification_service.notify(
                            user_id=_notif_user_id,
                            type="task_complete",
                            title=f"Task completed: {spec_id}",
                            message=f"Task {spec_id} in project {_proj_name} completed successfully.",
                            data={"task_id": task_id, "project_id": _proj_id},
                        )
                    except Exception:
                        logger.debug("Failed to send task completion notification", exc_info=True)
            else:
                logger.error(f"[AgentService] Task {task_id} failed with exit code {return_code}")
                await self._emit_progress(
                    TaskProgress(
                        task_id=task_id,
                        phase=TaskPhase.FAILED,
                        message=f"Task failed with exit code {return_code}",
                    ),
                    previous_phase=actual_phase,  # Enable status event emission
                )
                if _notif_user_id:
                    try:
                        from .notification_service import notification_service
                        _proj_name = project_path.name if project_path else ""
                        _proj_id = task_id.split(":")[0] if ":" in task_id else ""
                        await notification_service.notify(
                            user_id=_notif_user_id,
                            type="task_failed",
                            title=f"Task failed: {spec_id}",
                            message=f"Task {spec_id} in project {_proj_name} failed with exit code {return_code}.",
                            data={"task_id": task_id, "project_id": _proj_id},
                        )
                    except Exception:
                        logger.debug("Failed to send task failure notification", exc_info=True)

            # Epic #44 R1 — reap the rmux session if the feature was on.
            # Idempotent + no-op when flag is unset, so safe on every path.
            from ..rmux.integration import reap_if_enabled as _rmux_reap
            _reap_spec_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
            try:
                await _rmux_reap(_reap_spec_id)
            except Exception:
                logger.warning(f"[AgentService] rmux reap hook raised (ignored); spec_id={_reap_spec_id}")

            # Clean up tracking data AFTER all emissions are complete
            # This must happen after _emit_progress so it can still read
            # _spec_dirs, _task_sequence_numbers, and _task_start_times
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]
            self._task_sequence_numbers.pop(task_id, None)
            self._last_emitted_task_update.pop(task_id, None)
            self._task_start_times.pop(task_id, None)
            self._task_current_phases.pop(task_id, None)
            self._task_profiles.pop(task_id, None)
            self._task_rate_limits.pop(task_id, None)
            self._task_subtask_states.pop(task_id, None)
            self._spec_dirs.pop(task_id, None)
        except asyncio.CancelledError:
            # Task was cancelled, cleanup already handled by stop_task
            pass
        except Exception as e:
            # Unexpected error, ensure cleanup
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]
            self._task_sequence_numbers.pop(task_id, None)
            self._last_emitted_task_update.pop(task_id, None)
            self._task_start_times.pop(task_id, None)
            self._task_current_phases.pop(task_id, None)
            self._task_user_ids.pop(task_id, None)
            self._task_profiles.pop(task_id, None)
            self._task_rate_limits.pop(task_id, None)
            self._task_subtask_states.pop(task_id, None)
            self._spec_dirs.pop(task_id, None)
            await self._emit_progress(TaskProgress(
                task_id=task_id,
                phase=TaskPhase.FAILED,
                message=f"Task monitoring error: {e}",
            ))

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
        plan_file = project_path / ".pfactory" / "specs" / spec_id / "test_plan.json"
        logger.info(f"[AgentService._update_plan_status] CALLED for spec_id={spec_id}, status={status}, task_id={task_id}")
        logger.info(f"[AgentService._update_plan_status] plan_file path: {plan_file}")
        logger.info(f"[AgentService._update_plan_status] plan_file exists: {plan_file.exists()}")
        if not plan_file.exists():
            logger.warning("[AgentService._update_plan_status] plan_file does not exist, returning early")
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
                logger.info(f"[AgentService._update_plan_status] Plan status is 'done' (user-set), skipping overwrite for {spec_id}")
                return

            # Fix 2: Validate that the plan is not just a minimal status object
            # A valid plan should have phases and subtasks from spec creation
            if "phases" not in plan or not plan.get("phases"):
                logger.error(f"[AgentService] Invalid or minimal implementation plan detected for {spec_id}")
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

            logger.info(f"[AgentService._update_plan_status] About to write file with status={plan.get('status')}, reviewReason={plan.get('reviewReason')}")
            plan_file.write_text(json.dumps(plan, indent=2))
            logger.info("[AgentService._update_plan_status] Successfully wrote plan_file")
            logger.info(f"[AgentService] Updated plan status to '{plan['status']}' for {spec_id}")

            # Extract subtasks for WebSocket broadcast
            subtasks_data = []
            phases = plan.get("phases", [])
            for phase in phases:
                phase_subtasks = phase.get("subtasks", [])
                for subtask in phase_subtasks:
                    subtasks_data.append({
                        "id": subtask.get("id", ""),
                        "status": subtask.get("status", "pending"),
                        "title": subtask.get("description", ""),
                    })

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
            logger.error(f"[AgentService] Failed to update plan status: {e}")
            # Still emit status event so frontend updates even if plan file write failed
            if emit_events:
                try:
                    fallback_status = phase_to_status(phase_enum) if phase_enum else status
                    fallback_reason = phase_to_review_reason(phase_enum) if phase_enum else None
                    await self._safe_emit_task_status(task_id, fallback_status, fallback_reason)
                except Exception:
                    logger.error(f"[AgentService] Failed to emit fallback task:status for {task_id}")

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
            _, spec_id = task_id.split(":", 1)
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
                    import json
                    metadata = json.loads(task_metadata_file.read_text())
                    if metadata.get("requireReviewBeforeCoding", False):
                        should_auto_approve = False
                        logger.info(f"[AgentService] Task {task_id} requires manual review - NOT auto-approving spec")
                    # Read spec phase model from auto profile config
                    if metadata.get("isAutoProfile") and metadata.get("phaseModels"):
                        spec_phase_model = metadata["phaseModels"].get("spec")
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[AgentService] Failed to read task_metadata.json: {e}")

        # Build command
        cmd = [
            sys.executable,
            str(self.backend_path / "runners" / "spec_runner.py"),
            "--task", f"{title}\n\n{description}",
            "--project-dir", str(project_path),
        ]

        # Pass spec phase model if configured (multi-model support)
        if spec_phase_model:
            cmd.extend(["--model", spec_phase_model])
            logger.info(f"[AgentService] [Model: {spec_phase_model}] Starting spec creation for {task_id}")
        else:
            logger.info(f"[AgentService] [Model: sonnet] Starting spec creation for {task_id} (default)")

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
            logger.info(f"[AgentService] Quick Mode enabled for spec creation task {task_id}")

        # Load backend .env file for graphiti and other settings
        backend_env_file = self.backend_path / ".env"
        if backend_env_file.exists():
            try:
                with open(backend_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            # Don't override existing env vars
                            if key not in env:
                                env[key] = value
                logger.info("[AgentService] Loaded backend .env for spec creation")
            except Exception as e:
                logger.warning(f"[AgentService] Failed to load backend .env: {e}")

        # Load project .pfactory/.env for project-level settings (USE_CLAUDE_MD, etc.)
        project_env_file = project_path / ".pfactory" / ".env"
        if project_env_file.exists():
            try:
                with open(project_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            if key not in env:
                                env[key] = value
                logger.info("[AgentService] Loaded project .env for spec creation")
            except Exception as e:
                logger.warning(f"[AgentService] Failed to load project .env: {e}")

        # Get OAuth token with profile tracking
        token, profile_id, profile_name = self._resolve_claude_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(
                f"[AgentService] Using Claude profile for spec creation: {profile_name} ({profile_id})"
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
        await self._emit_progress(TaskProgress(
            task_id=task_id,
            phase=TaskPhase.SPEC_CREATION,
            message="Starting spec creation...",
            percentage=50,
        ))

        # Start output processing in background
        asyncio.create_task(self._process_output(task_id, proc.stdout, is_stderr=False))
        asyncio.create_task(self._process_output(task_id, proc.stderr, is_stderr=True))

        # Start process monitor to clean up when finished
        # Pass project_path so monitor can detect created spec and check for review state
        # Pass cmd and env so model fallback can retry with a different model on failure
        asyncio.create_task(self._monitor_process(task_id, proc, project_path=project_path, cmd=cmd, env=env))

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

        if task_id in self.running_tasks:
            raise ValueError(f"Task {task_id} is already running")

        # Build command
        cmd = [
            sys.executable,
            str(self.backend_path / "run.py"),
            "--spec", spec_id,
            "--project-dir", str(project_path),
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
                    import json
                    requirements = json.loads(requirements_file.read_text())
                    frontend_metadata = requirements.get("metadata", {})

                    # Read existing task_metadata or create new
                    if task_metadata_file.exists():
                        task_metadata = json.loads(task_metadata_file.read_text())
                    else:
                        task_metadata = {}

                    # Sync requireReviewBeforeCoding from frontend to backend
                    if "requireReviewBeforeCoding" in frontend_metadata:
                        task_metadata["requireReviewBeforeCoding"] = frontend_metadata["requireReviewBeforeCoding"]

                    # Save updated task_metadata.json
                    task_metadata_file.write_text(json.dumps(task_metadata, indent=2))

                    require_review = task_metadata.get("requireReviewBeforeCoding", False)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[AgentService] Could not sync metadata for {task_id}: {e}")
            elif task_metadata_file.exists():
                try:
                    import json
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
                    logger.info(f"[AgentService] Using --force for {task_id} (plan manually approved)")
            else:
                logger.info(f"[AgentService] Human review before coding enabled for task {task_id} - not using --force")

        if base_branch:
            cmd.extend(["--base-branch", base_branch])

        # Skip QA for quick mode (simple tasks) - coder_quick.md validates inline
        if mode == "quick":
            cmd.append("--skip-qa")
            logger.info(f"[AgentService] Skipping QA for quick mode task {task_id}")

        # Stop after planning for Copilot delegation flow (#94)
        if stop_after_planning:
            cmd.append("--stop-after-planning")
            logger.info(f"[AgentService] Stop-after-planning for {task_id} (Copilot delegation)")

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
            logger.info(f"[AgentService] Quick Mode enabled for task {task_id}")

        # Load backend .env file for graphiti and other settings
        backend_env_file = self.backend_path / ".env"
        if backend_env_file.exists():
            try:
                with open(backend_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            # Don't override existing env vars
                            if key not in env:
                                env[key] = value
                logger.info(f"[AgentService] Loaded backend .env from {backend_env_file}")
            except Exception as e:
                logger.warning(f"[AgentService] Failed to load backend .env: {e}")

        # Load project .pfactory/.env for project-level settings (USE_CLAUDE_MD, etc.)
        project_env_file = project_path / ".pfactory" / ".env"
        if project_env_file.exists():
            try:
                with open(project_env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            if key not in env:
                                env[key] = value
                logger.info("[AgentService] Loaded project .env for task execution")
            except Exception as e:
                logger.warning(f"[AgentService] Failed to load project .env: {e}")

        # Get OAuth token with profile tracking
        token, profile_id, profile_name = self._resolve_claude_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(f"[AgentService] Using Claude profile: {profile_name} ({profile_id})")
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
        logger.info(f"[AgentService] [Model: {exec_model_display}] Starting task execution for {task_id}")
        logger.info(f"[AgentService] Command: {' '.join(cmd)}")

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
                "[AgentService] Remote Control ENABLED for task_id=%s — "
                "session %r will appear in claude.ai/code. "
                "Scrubbed CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_AUTH_TOKEN — "
                "agent will fall back to ~/.claude/.credentials.json "
                "(must be a full-scope token from `claude auth login`).",
                task_id, _rc_session_name,
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
                "[AgentService] PFACTORY_TEST_AGENT_CMD active — replacing "
                "agent command with %r (task_id=%s). MUST NOT be set in prod.",
                cmd, task_id,
            )

        # Start subprocess with a pseudo-TTY to prevent "Stream closed" errors
        # Claude Code CLI expects a TTY for permission handling
        import pty

        master_fd, slave_fd = pty.openpty()

        # Tee stderr to a per-spec file so failures that happen before
        # the agent writes task_logs.json are still debuggable (#146).
        # _process_output still drains the PIPE; this is an additional
        # post-mortem capture, not a replacement.
        spec_stderr_log = (
            project_path / ".pfactory" / "specs" / spec_id / "spawn_stderr.log"
        )
        try:
            spec_stderr_log.parent.mkdir(parents=True, exist_ok=True)
            spec_stderr_log.write_text("")  # truncate any previous capture
        except OSError as _e:
            logger.debug(f"[AgentService] could not prep spawn_stderr.log: {_e}")

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
        worktree_spec_dir = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id / ".pfactory" / "specs" / spec_id
        worktree_spec_dir.mkdir(parents=True, exist_ok=True)
        log_writer = TaskLogWriter(worktree_spec_dir)

        # Also write to main spec dir for immediate visibility
        main_spec_dir = project_path / ".pfactory" / "specs" / spec_id
        main_spec_dir.mkdir(parents=True, exist_ok=True)
        main_log_writer = TaskLogWriter(main_spec_dir)

        # Store log writers for cleanup
        self._task_log_writers[task_id] = (log_writer, main_log_writer)

        # Emit initial progress (100% within planning phase → 20% overall)
        await self._emit_progress(TaskProgress(
            task_id=task_id,
            phase=TaskPhase.PLANNING,
            message="Starting task execution...",
            percentage=100,
        ))

        # Initialize planning phase in logs
        log_writer.set_phase_status(spec_id, TaskPhase.PLANNING, "active")
        main_log_writer.set_phase_status(spec_id, TaskPhase.PLANNING, "active")

        # Start output processing in background with log writers
        asyncio.create_task(self._process_output(
            task_id, proc.stdout, is_stderr=False,
            log_writer=log_writer, spec_id=spec_id
        ))
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
            logger.warning(f"[AgentService] rmux create hook raised (ignored); spec_id={spec_id}")

        return proc

    async def stop_task(self, task_id: str) -> bool:
        """Stop a running task."""
        import logging
        logger = logging.getLogger(__name__)
        if task_id not in self.running_tasks:
            logger.info(f"[AgentService] Task {task_id} not in running_tasks (already stopped or never started)")
            return False

        # Mark as stopped BEFORE termination so _monitor_process defers to us
        self._task_stopped.add(task_id)

        proc = self.running_tasks[task_id]
        proc.terminate()

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        # Get actual phase and spec info BEFORE cleanup
        actual_phase = self._get_current_phase(task_id)
        spec_dir = self._spec_dirs.get(task_id)

        # Finalize log writers — flush pending text, mark phase as failed
        if task_id in self._task_log_writers:
            log_writer, main_log_writer = self._task_log_writers[task_id]
            # Parse spec_id from task_id (format: "project_id:spec_id")
            spec_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
            log_writer.finalize(spec_id, actual_phase)
            log_writer.set_phase_status(spec_id, actual_phase, "failed")
            main_log_writer.finalize(spec_id, actual_phase)
            main_log_writer.set_phase_status(spec_id, actual_phase, "failed")
            del self._task_log_writers[task_id]
            logger.debug(f"[AgentService] Finalized task logs for stopped task {task_id}")

        # Persist failed status to test_plan.json
        if spec_dir:
            # Derive project_path: spec_dir is .pfactory/specs/XXX, project root is 3 levels up
            project_path = spec_dir.parent.parent.parent
            spec_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
            await self._update_plan_status(project_path, spec_id, "failed", task_id)

        # Epic #44 R1 — reap rmux session if the feature was on. Idempotent
        # so safe even though _monitor_process may also reap on the natural
        # exit path.
        from ..rmux.integration import reap_if_enabled as _rmux_reap
        _reap_spec_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
        try:
            await _rmux_reap(_reap_spec_id)
        except Exception:
            logger.warning(f"[AgentService] rmux reap hook raised in stop_task (ignored); spec_id={_reap_spec_id}")

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
        await self._emit_progress(TaskProgress(
            task_id=task_id,
            phase=TaskPhase.FAILED,
            message="Task stopped by user",
        ))

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
            await self._emit_progress(TaskProgress(
                task_id=task_id,
                phase=TaskPhase.COMPLETED,
                message="Task completed successfully",
            ))
        else:
            await self._emit_progress(TaskProgress(
                task_id=task_id,
                phase=TaskPhase.FAILED,
                message=f"Task failed with exit code {return_code}",
            ))

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
