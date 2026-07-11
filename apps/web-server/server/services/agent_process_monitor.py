"""
Process-monitor mixin for AgentService.

Parses phase events from agent output, streams subprocess stdout/stderr into
logs and progress events, and monitors the subprocess lifecycle (periodic
worktree sync, review-checkpoint detection, profile/model failover retries,
auto-continuation, terminal emissions, tracking cleanup). Extracted verbatim
from ``agent_service.py`` (issue Factory#255 seam e, characterization tests
in ``tests/test_agent_monitor_characterization.py``); ``AgentService``
inherits this mixin, so behavior is unchanged.

The per-line ``noqa`` directives mark pre-existing violations carried over
verbatim (behavior-preserving move; legacy is fixed on touch per the ratchet
policy, not during extraction).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .task_models import TaskLog, TaskPhase, TaskProgress

if TYPE_CHECKING:
    from .task_log_writer import TaskLogWriter


class AgentProcessMonitorMixin:
    """Subprocess monitoring behavior mixed into ``AgentService``.

    Host-class dependencies (attributes and helpers defined on
    ``AgentService`` or its other mixins) are declared here for type
    checkers only.
    """

    if TYPE_CHECKING:
        running_tasks: dict[str, asyncio.subprocess.Process]
        _task_current_phases: dict[str, TaskPhase]
        _spec_stderr_logs: dict[str, Path]
        _task_rate_limits: dict[str, bool]
        _task_profiles: dict[str, dict[str, Any]]
        _task_log_writers: dict[str, tuple[TaskLogWriter, TaskLogWriter]]
        _task_stopped: set[str]
        _task_user_ids: dict[str, str]
        _task_sequence_numbers: dict[str, int]
        _last_emitted_task_update: dict[str, tuple[Any, ...]]
        _task_start_times: dict[str, str]
        _task_subtask_states: dict[str, dict[str, str]]
        _spec_dirs: dict[str, Path]

        def _get_current_phase(self, task_id: str) -> TaskPhase: ...
        async def _emit_log(self, log: TaskLog) -> None: ...
        async def _emit_progress(
            self, progress: TaskProgress, previous_phase: TaskPhase | None = None
        ) -> None: ...
        async def _sync_worktree_files(
            self, project_path: Path, spec_id: str, task_id: str | None = None
        ) -> None: ...
        def _is_rate_limit_line(self, line: str) -> bool: ...
        def _is_early_failure(self, spec_dir: Path, exit_code: int) -> bool: ...
        def _should_retry_with_failover(self) -> bool: ...
        async def _retry_task_with_fallback_model(
            self,
            task_id: str,
            project_path: Path,
            spec_id: str,
            cmd: list[str],
            env: dict[str, str],
        ) -> asyncio.subprocess.Process | None: ...
        async def _retry_task_with_profile(  # noqa: PLR0913
            self,
            task_id: str,
            project_path: Path,
            spec_id: str,
            cmd: list[str],
            env: dict[str, str],
            failed_profile_id: str,
            reason: str,
        ) -> asyncio.subprocess.Process | None: ...
        async def _update_plan_status(
            self,
            project_path: Path,
            spec_id: str,
            status: str,
            task_id: str,
            *,
            emit_events: bool = True,
        ) -> None: ...
        async def start_task_execution(  # noqa: PLR0913
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
        ) -> asyncio.subprocess.Process: ...

    def _parse_phase_event(self, line: str) -> dict[str, Any] | None:
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
                event = cast(dict[str, Any], json.loads(json_str))
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

    async def _process_output(  # noqa: PLR0912, PLR0915
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
        import logging  # noqa: PLC0415
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
                logger.warning(f"[AgentService] Rate limit detected for task {task_id} (will attempt failover if enabled)")  # noqa: E501

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
                        # For COMPLETED/FAILED phases, don't set them as "active" - just mark previous complete  # noqa: E501
                        if current_phase not in (TaskPhase.COMPLETED, TaskPhase.FAILED):
                            log_writer.set_phase_status(spec_id, current_phase, "active")
                        # Ensure validation phase is properly marked completed when task completes
                        if current_phase == TaskPhase.COMPLETED and old_phase in (TaskPhase.QA_REVIEW, TaskPhase.QA_FIXING):  # noqa: E501
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
                await self._emit_progress(progress, previous_phase=old_phase if old_phase != current_phase else None)  # noqa: E501

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
                        await self._emit_progress(progress, previous_phase=old_phase if old_phase != current_phase else None)  # noqa: E501
                except json.JSONDecodeError:
                    pass

        return current_phase

    async def _monitor_process(  # noqa: PLR0911, PLR0912, PLR0913, PLR0915
        self,
        task_id: str,
        proc: asyncio.subprocess.Process,
        project_path: Path | None = None,
        spec_id: str | None = None,
        cmd: list[str] | None = None,
        env: dict[str, str] | None = None
    ) -> None:
        """Monitor subprocess and clean up when it finishes.

        Also periodically syncs files from worktree to main spec dir if project_path and spec_id are provided.
        Supports profile failover on early failures when cmd and env are provided.
        """  # noqa: E501
        import logging  # noqa: PLC0415
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
                except asyncio.TimeoutError:  # noqa: UP041
                    # Process still running, sync files
                    if project_path and spec_id:
                        await self._sync_worktree_files(project_path, spec_id, task_id)

                    # Fix Bug #3: For spec creation, check if review checkpoint reached while process is running  # noqa: E501
                    if project_path and not spec_id:
                        # Detect if spec_runner created plan_review.html (review checkpoint reached)
                        # Parse spec_id from task_id (format: "project_id:spec_id")
                        detected_spec_id = None
                        if ":" in task_id:
                            _, detected_spec_id = task_id.split(":", 1)

                        if detected_spec_id:
                            detected_spec_dir = project_path / ".pfactory" / "specs" / detected_spec_id  # noqa: E501
                            plan_review_file = detected_spec_dir / "plan_review.html"

                            # Check if plan_review.html exists (indicates review checkpoint reached)
                            if plan_review_file.exists():
                                # Check if we've already emitted PLAN_REVIEW for this task
                                current_phase = self._task_current_phases.get(task_id)
                                if current_phase != TaskPhase.PLAN_REVIEW:
                                    logger.info(f"[AgentService] Detected review checkpoint for {detected_spec_id} (plan_review.html exists)")  # noqa: E501

                                    # Update plan status to human_review
                                    await self._update_plan_status(project_path, detected_spec_id, "human_review", task_id)  # noqa: E501

                                    # Emit PLAN_REVIEW phase (maps to "human_review" status) — plan_review always scales to 20%  # noqa: E501
                                    await self._emit_progress(
                                        TaskProgress(
                                            task_id=task_id,
                                            phase=TaskPhase.PLAN_REVIEW,
                                            message="Spec created - waiting for human approval",
                                            percentage=100,
                                        ),
                                        previous_phase=TaskPhase.SPEC_CREATION,  # Enable status event emission  # noqa: E501
                                    )

                                    # Mark phase as emitted
                                    self._task_current_phases[task_id] = TaskPhase.PLAN_REVIEW
                                    logger.info(f"[AgentService] Emitted PLAN_REVIEW status for {task_id}")  # noqa: E501

                    # If we detect a rate limit and failover is enabled, don't wait for the process to exit.  # noqa: E501
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
                                f"[AgentService] Rate limit detected for {task_id} while running; terminating process to trigger profile failover"  # noqa: E501
                            )
                            rate_limit_forced_restart = True
                            try:  # noqa: SIM105
                                proc.terminate()
                            except Exception:  # noqa: BLE001, S110
                                pass
                            try:
                                return_code = await proc.wait()
                            except Exception:  # noqa: BLE001
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
            logger.info(f"[AgentService] [Model: {exit_model}] Task {task_id} process exited with code {return_code}")  # noqa: E501

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
                logger.info(f"[AgentService] Fallback check: model={_fb_model!r}, attempt={_fb_attempt}, is_non_claude={_fb_is_non_claude}, cmd={'yes' if cmd else 'no'}, env={'yes' if env else 'no'}")  # noqa: E501
                if _fb_is_non_claude and _fb_attempt <= 1:
                    new_proc = await self._retry_task_with_fallback_model(
                        task_id, project_path, spec_id, cmd, env  # type: ignore[arg-type]
                    )
                    if new_proc:
                        self._task_rate_limits.pop(task_id, None)
                        self.running_tasks[task_id] = new_proc

                        log_writer = None
                        main_log_writer = None
                        if task_id in self._task_log_writers:
                            log_writer, main_log_writer = self._task_log_writers[task_id]

                        asyncio.create_task(  # noqa: RUF006
                            self._process_output(
                                task_id, new_proc.stdout, is_stderr=False,  # type: ignore[arg-type]
                                log_writer=log_writer, spec_id=spec_id,
                            )
                        )
                        asyncio.create_task(  # noqa: RUF006
                            self._process_output(
                                task_id, new_proc.stderr, is_stderr=True,  # type: ignore[arg-type]
                                log_writer=log_writer, spec_id=spec_id,
                            )
                        )
                        asyncio.create_task(  # noqa: RUF006
                            self._monitor_process(
                                task_id, new_proc, project_path, spec_id,
                                cmd=None, env=None
                            )
                        )
                        logger.info(f"[AgentService] Task {task_id} restarted with fallback model (sonnet)")  # noqa: E501
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
                                    logger.info(f"[AgentService] Spec {detected_spec_id} requires human review")  # noqa: E501

                                    # Update plan status to human_review
                                    await self._update_plan_status(project_path, detected_spec_id, "human_review", task_id)  # noqa: E501

                                    # Clean up tracking data
                                    if task_id in self.running_tasks:
                                        del self.running_tasks[task_id]
                                    self._task_sequence_numbers.pop(task_id, None)
                                    self._last_emitted_task_update.pop(task_id, None)
                                    self._task_start_times.pop(task_id, None)
                                    self._task_current_phases.pop(task_id, None)
                                    self._task_profiles.pop(task_id, None)
                                    self._task_subtask_states.pop(task_id, None)

                                    # Emit PLAN_REVIEW phase (maps to "human_review" status) — plan_review always scales to 20%  # noqa: E501
                                    await self._emit_progress(
                                        TaskProgress(
                                            task_id=task_id,
                                            phase=TaskPhase.PLAN_REVIEW,
                                            message="Spec created - waiting for human approval",
                                            percentage=100,
                                        ),
                                        previous_phase=TaskPhase.SPEC_CREATION,  # Enable status event emission  # noqa: E501
                                    )

                                    logger.info(f"[AgentService] Spec {detected_spec_id} transitioned to PLAN_REVIEW phase")  # noqa: E501
                                    return  # Exit early - not a failure

                            # If we reach here, spec was created but doesn't need review
                            # Auto-start task execution immediately
                            logger.info(f"[AgentService] Spec {detected_spec_id} created successfully (no review required) — auto-starting execution")  # noqa: E501

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
                                logger.info(f"[AgentService] Task execution auto-started for {detected_spec_id}")  # noqa: E501
                            except Exception as exec_err:  # noqa: BLE001
                                logger.error(f"[AgentService] Failed to auto-start execution for {detected_spec_id}: {exec_err}")  # noqa: E501
                                # Fall back to human_review status so user can start manually
                                await self._update_plan_status(project_path, detected_spec_id, "completed", task_id)  # noqa: E501
                            return  # Exit early
                except Exception as e:  # noqa: BLE001
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
                            logger.info(f"[AgentService] Task {task_id} awaiting human review (not a failure)")  # noqa: E501

                            # Get actual phase BEFORE cleanup
                            actual_phase = self._get_current_phase(task_id)

                            # Finalize log writers for the phase we were in
                            if task_id in self._task_log_writers:
                                log_writer, main_log_writer = self._task_log_writers[task_id]
                                if spec_id:
                                    log_writer.finalize(spec_id, actual_phase)
                                    log_writer.set_phase_status(spec_id, actual_phase, "completed")
                                    main_log_writer.finalize(spec_id, actual_phase)
                                    main_log_writer.set_phase_status(spec_id, actual_phase, "completed")  # noqa: E501
                                del self._task_log_writers[task_id]

                            # Update plan status to human_review
                            await self._update_plan_status(project_path, spec_id, "human_review", task_id)  # noqa: E501

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
                            # If task was still planning, it just finished planning → show 20% progress  # noqa: E501
                            if actual_phase in (TaskPhase.CODING, TaskPhase.QA_REVIEW, TaskPhase.QA_FIXING, TaskPhase.COMPLETED):  # noqa: E501
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

                            logger.info(f"[AgentService] Task {task_id} transitioned to {emit_phase.value} phase (was {actual_phase.value})")  # noqa: E501
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
                    logger.info(f"[AgentService] {reason.replace('_', ' ')} detected for {task_id}, attempting profile failover")  # noqa: E501

                    # Attempt retry with different profile
                    if not failed_profile_id:
                        logger.warning(f"[AgentService] No failed profile recorded for {task_id}; cannot failover")  # noqa: E501
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
                        asyncio.create_task(  # noqa: RUF006
                            self._process_output(
                                task_id,
                                new_proc.stdout,  # type: ignore[arg-type]
                                is_stderr=False,
                                log_writer=log_writer,
                                spec_id=spec_id,
                            )
                        )
                        asyncio.create_task(  # noqa: RUF006
                            self._process_output(
                                task_id,
                                new_proc.stderr,  # type: ignore[arg-type]
                                is_stderr=True,
                                log_writer=log_writer,
                                spec_id=spec_id,
                            )
                        )

                        # Restart monitoring for new subprocess (without cmd/env to prevent infinite retry)  # noqa: E501
                        asyncio.create_task(  # noqa: RUF006
                            self._monitor_process(
                                task_id,
                                new_proc,
                                project_path,
                                spec_id,
                                cmd=None,  # Prevent second retry
                                env=None   # Prevent second retry
                            )
                        )

                        logger.info(f"[AgentService] Task {task_id} restarted with alternate profile")  # noqa: E501
                        return  # Exit this monitor instance
                    else:
                        logger.warning(f"[AgentService] No alternate profile available for task {task_id}, trying model fallback")  # noqa: E501


            # If stop_task() already handled cleanup, skip duplicate processing
            if task_id in self._task_stopped:
                self._task_stopped.discard(task_id)
                logger.info(f"[AgentService] Task {task_id} was stopped by user, skipping _monitor_process cleanup")  # noqa: E501
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

                        if pending_count > 0 and round_num <= 10:  # noqa: PLR2004
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
                                logger.info(f"[AgentService] Auto-continuation started for {spec_id} (round {round_num})")  # noqa: E501
                                return  # Exit this monitor — new monitor will take over
                            except Exception as e:  # noqa: BLE001
                                logger.error(f"[AgentService] Auto-continuation failed for {spec_id}: {e}")  # noqa: E501
                                # Fall through to normal completion
                        elif pending_count > 0 and round_num > 10:  # noqa: PLR2004
                            logger.warning(
                                f"[AgentService] Auto-continuation limit reached (10 rounds) for {spec_id}, "  # noqa: E501
                                f"{pending_count} subtasks still pending"
                            )
                        else:
                            # All subtasks done — clean up continuation tracker
                            if hasattr(self, continuation_key):
                                delattr(self, continuation_key)
                            logger.info(f"[AgentService] All {total_count} subtasks completed for {spec_id}")  # noqa: E501
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning(f"[AgentService] Could not check subtask status for auto-continuation: {e}")  # noqa: E501

            # Update test_plan.json status for frontend display.
            # emit_events=False (Issue #14): the subsequent _emit_progress
            # call at lines ~1830/1856 is the SINGLE canonical terminal
            # emission. Letting _update_plan_status also emit produced the
            # 5-event flurry + phase:N/A blip — kept the file write here,
            # moved the WebSocket events to the explicit _emit_progress.
            if spec_id and project_path:
                status = "completed" if return_code == 0 else "failed"
                logger.info(f"[AgentService._monitor_process] About to call _update_plan_status: spec_id={spec_id}, status={status}, task_id={task_id}, project_path={project_path}")  # noqa: E501
                await self._update_plan_status(
                    project_path, spec_id, status, task_id, emit_events=False
                )
                logger.info("[AgentService._monitor_process] _update_plan_status call completed")

            # Send email/in-app notifications on task completion or failure
            _notif_user_id = self._task_user_ids.pop(task_id, "")

            # Emit completion/failure progress with previous_phase to trigger status event
            # NOTE: Cleanup is deferred until AFTER these emissions so _emit_progress
            # can still read _spec_dirs (for plan file), _task_sequence_numbers, and _task_start_times  # noqa: E501
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
                        from .notification_service import notification_service  # noqa: PLC0415
                        _proj_name = project_path.name if project_path else ""
                        _proj_id = task_id.split(":")[0] if ":" in task_id else ""  # noqa: PLC0207
                        await notification_service.notify(
                            user_id=_notif_user_id,
                            type="task_complete",
                            title=f"Task completed: {spec_id}",
                            message=f"Task {spec_id} in project {_proj_name} completed successfully.",  # noqa: E501
                            data={"task_id": task_id, "project_id": _proj_id},
                        )
                    except Exception:  # noqa: BLE001
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
                        from .notification_service import notification_service  # noqa: PLC0415
                        _proj_name = project_path.name if project_path else ""
                        _proj_id = task_id.split(":")[0] if ":" in task_id else ""  # noqa: PLC0207
                        await notification_service.notify(
                            user_id=_notif_user_id,
                            type="task_failed",
                            title=f"Task failed: {spec_id}",
                            message=f"Task {spec_id} in project {_proj_name} failed with exit code {return_code}.",  # noqa: E501
                            data={"task_id": task_id, "project_id": _proj_id},
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("Failed to send task failure notification", exc_info=True)

            # Epic #44 R1 — reap the rmux session if the feature was on.
            # Idempotent + no-op when flag is unset, so safe on every path.
            from ..rmux.integration import reap_if_enabled as _rmux_reap  # noqa: PLC0415, TID252
            _reap_spec_id = task_id.split(":", 1)[1] if ":" in task_id else task_id
            try:
                await _rmux_reap(_reap_spec_id)
            except Exception:  # noqa: BLE001
                logger.warning(f"[AgentService] rmux reap hook raised (ignored); spec_id={_reap_spec_id}")  # noqa: E501

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
        except Exception as e:  # noqa: BLE001
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
