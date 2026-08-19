"""
PR review execution service.

Wraps the GitHub runner's review-pr / followup-review-pr commands as an async
service, enabling PR reviews with real-time progress streaming via WebSocket.

Follows the same subprocess + WebSocket pattern as agent_service.py:
- Async subprocess management with concurrent stdout/stderr reading
- Phase detection from runner output
- WebSocket event broadcasting for real-time frontend updates
- Execution log writing for the /prs/{prNumber}/logs endpoint
- Singleton pattern with global getter
"""

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from factory_common.logsafe import sanitize_log
from server.error_ref import error_reference

from ..config import get_settings
from ..websockets.events import broadcast_event


class PRReviewPhase(str, Enum):
    """PR review execution phases."""

    STARTING = "starting"
    FETCHING = "fetching"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    COMPLETE = "complete"
    FAILED = "failed"


# Phase progress percentages
PHASE_PROGRESS = {
    PRReviewPhase.STARTING: 0,
    PRReviewPhase.FETCHING: 15,
    PRReviewPhase.ANALYZING: 40,
    PRReviewPhase.GENERATING: 75,
    PRReviewPhase.COMPLETE: 100,
    PRReviewPhase.FAILED: 0,
}

# Pattern matching for progress detection from runner stdout
# Runner outputs: [PR #N] [XXX%] message
PROGRESS_PATTERN = re.compile(r"\[PR\s*#\d+\]\s*\[\s*(\d+)%\]\s*(.*)")


@dataclass
class PRReviewProgress:
    """PR review progress information."""

    project_id: str
    pr_number: int
    phase: PRReviewPhase
    progress: int
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


logger = logging.getLogger(__name__)


def _load_env_file(env_file: Path, env: dict) -> None:
    """Load key=value pairs from an .env file into env dict (without overwriting)."""
    if not env_file.exists():
        return
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k not in env:
                        env[k] = v
    except Exception as e:
        logger.warning("Failed to load env file %s: %s", sanitize_log(env_file), sanitize_log(e))


class PRReviewLogWriter:
    """Writes phase-level review execution logs to review_{prNumber}_logs.json.

    This enables the GET /prs/{prNumber}/logs endpoint to return per-phase
    timing and log entries for the review execution.
    """

    def __init__(self, logs_file: Path, pr_number: int):
        self._logs_file = logs_file
        self._pr_number = pr_number
        self._data: dict = {
            "prNumber": pr_number,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat(),
            "phases": {},
        }

    def start_phase(self, phase: PRReviewPhase) -> None:
        """Mark a phase as started."""
        self._data["phases"][phase.value] = {
            "phase": phase.value,
            "status": "active",
            "startedAt": datetime.now().isoformat(),
            "completedAt": None,
            "entries": [],
        }
        self._save()

    def add_entry(self, phase: PRReviewPhase, message: str, progress: int = 0) -> None:
        """Add a log entry to a phase."""
        phase_key = phase.value
        if phase_key not in self._data["phases"]:
            self.start_phase(phase)

        self._data["phases"][phase_key]["entries"].append(
            {
                "timestamp": datetime.now().isoformat(),
                "message": message,
                "progress": progress,
            }
        )
        self._save()

    def complete_phase(self, phase: PRReviewPhase, status: str = "completed") -> None:
        """Mark a phase as completed or failed."""
        phase_key = phase.value
        if phase_key in self._data["phases"]:
            self._data["phases"][phase_key]["status"] = status
            self._data["phases"][phase_key]["completedAt"] = datetime.now().isoformat()
            self._save()

    def finalize(self, status: str = "completed") -> None:
        """Finalize the log with overall status."""
        self._data["status"] = status
        self._data["completedAt"] = datetime.now().isoformat()
        self._save()

    def _save(self) -> None:
        """Save the log data to disk."""
        self._data["updatedAt"] = datetime.now().isoformat()
        try:
            self._logs_file.parent.mkdir(parents=True, exist_ok=True)
            self._logs_file.write_text(json.dumps(self._data, indent=2))
        except OSError as e:
            logger.warning("Failed to write review logs: %s", sanitize_log(e))


class PRReviewService:
    """Service for managing async PR review execution.

    Follows the agent_service.py pattern:
    - Tracks running subprocesses by composite key (project_id:pr_number)
    - Processes stdout/stderr concurrently
    - Emits WebSocket events for real-time frontend updates
    - Writes execution logs for the logs endpoint
    - Singleton pattern via get_pr_review_service()
    """

    def __init__(self):
        self.running_reviews: dict[str, asyncio.subprocess.Process] = {}
        self._current_phases: dict[str, PRReviewPhase] = {}
        self._log_writers: dict[str, PRReviewLogWriter] = {}
        self._review_start_times: dict[str, str] = {}

    def _review_key(self, project_id: str, pr_number: int) -> str:
        """Create a unique key for a project + PR combination."""
        return f"{project_id}:{pr_number}"

    def is_running(self, project_id: str, pr_number: int) -> bool:
        """Check if a review is running for this project + PR."""
        return self._review_key(project_id, pr_number) in self.running_reviews

    def get_status(self, project_id: str, pr_number: int) -> dict:
        """Get the current status for a PR review."""
        key = self._review_key(project_id, pr_number)
        if key not in self.running_reviews:
            return {
                "isRunning": False,
                "status": "idle",
                "progress": 0,
                "message": None,
            }

        phase = self._current_phases.get(key, PRReviewPhase.STARTING)
        return {
            "isRunning": True,
            "status": phase.value,
            "progress": PHASE_PROGRESS.get(phase, 0),
            "message": f"Running: {phase.value.replace('_', ' ').title()}",
            "startedAt": self._review_start_times.get(key),
        }

    async def start_review(
        self,
        project_id: str,
        pr_number: int,
        project_path: Path,
        followup: bool = False,
    ) -> bool:
        """Start a PR review as an async subprocess.

        Args:
            project_id: The project identifier.
            pr_number: The PR number to review.
            project_path: Filesystem path to the project.
            followup: If True, run a follow-up review instead of initial review.

        Returns:
            True if the review was started, False if already running.
        """
        key = self._review_key(project_id, pr_number)
        if key in self.running_reviews:
            logger.warning("PR review already running for %s", sanitize_log(key))
            return False

        settings = get_settings()
        backend_path = Path(settings.BACKEND_PATH)
        runner_script = backend_path / "runners" / "github" / "runner.py"

        if not runner_script.exists():
            logger.error("GitHub runner not found at %s", sanitize_log(runner_script))
            await self._emit_error(project_id, pr_number, "GitHub runner not found")
            return False

        # Build command
        command = "followup-review-pr" if followup else "review-pr"
        cmd = [
            sys.executable,
            str(runner_script),
            "--project",
            str(project_path),
            command,
            str(pr_number),
        ]

        logger.info("Starting PR review for %s: %s", sanitize_log(key), sanitize_log(" ".join(cmd)))

        # Set up environment — scrub ANTHROPIC_API_KEY (OAuth-only policy).
        from ..utils.subprocess_env import make_subprocess_env

        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        # Add backend path to PYTHONPATH for imports
        existing_pythonpath = env.get("PYTHONPATH", "")
        backend_pythonpath = str(backend_path)
        github_runner_path = str(backend_path / "runners" / "github")
        if existing_pythonpath:
            env["PYTHONPATH"] = f"{backend_pythonpath}:{github_runner_path}:{existing_pythonpath}"
        else:
            env["PYTHONPATH"] = f"{backend_pythonpath}:{github_runner_path}"

        # Load backend .env for tokens and API keys
        _load_env_file(backend_path / ".env", env)

        # Load project-level .pfactory/.env for project settings
        _load_env_file(project_path / ".pfactory" / ".env", env)

        # Initialize execution log writer
        logs_dir = project_path / ".pfactory" / "github" / "pr"
        logs_file = logs_dir / f"review_{pr_number}_logs.json"
        log_writer = PRReviewLogWriter(logs_file, pr_number)
        self._log_writers[key] = log_writer

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_path),
                env=env,
            )

            self.running_reviews[key] = proc
            self._current_phases[key] = PRReviewPhase.STARTING
            self._review_start_times[key] = datetime.now().isoformat()

            # Log the start phase
            log_writer.start_phase(PRReviewPhase.STARTING)
            log_writer.add_entry(
                PRReviewPhase.STARTING,
                f"Starting {'follow-up ' if followup else ''}review for PR #{pr_number}",
            )

            # Emit initial progress
            await self._emit_progress(
                project_id,
                pr_number,
                PRReviewPhase.STARTING,
                "Starting PR review...",
            )

            # Process output in background
            asyncio.create_task(self._process_output(project_id, pr_number, project_path, proc))

            return True

        except Exception as e:
            logger.error("Failed to start PR review: %s", sanitize_log(e))
            # _emit_error and the log writer both reach the browser (WebSocket
            # event / review-log panel), so the exception text cannot go in
            # either - it names the workspace path and the coder binary (CWE-209).
            ref = error_reference(logger, f"start PR review for #{pr_number}", e)
            log_writer.add_entry(PRReviewPhase.FAILED, f"Failed to start (reference {ref})")
            log_writer.finalize("failed")
            await self._emit_error(
                project_id, pr_number, f"the review could not be started (reference {ref})"
            )
            self._cleanup(key)
            return False

    async def cancel_review(self, project_id: str, pr_number: int) -> bool:
        """Cancel a running PR review."""
        key = self._review_key(project_id, pr_number)
        if key not in self.running_reviews:
            return False

        proc = self.running_reviews.get(key)
        if proc:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
            except Exception as e:
                logger.error("Error cancelling PR review: %s", sanitize_log(e))

        # Finalize logs for cancelled review
        log_writer = self._log_writers.get(key)
        if log_writer:
            current_phase = self._current_phases.get(key, PRReviewPhase.STARTING)
            log_writer.add_entry(current_phase, "Review cancelled by user")
            log_writer.complete_phase(current_phase, "cancelled")
            log_writer.finalize("cancelled")

        self._cleanup(key)
        await self._emit_error(project_id, pr_number, "Review cancelled by user")
        return True

    async def _process_output(
        self,
        project_id: str,
        pr_number: int,
        project_path: Path,
        proc: asyncio.subprocess.Process,
    ):
        """Process subprocess output and emit progress events.

        Reads stdout/stderr concurrently (following agent_service.py pattern),
        parses progress patterns, emits WebSocket events, and writes execution logs.
        """
        key = self._review_key(project_id, pr_number)
        stderr_lines: list[str] = []
        log_writer = self._log_writers.get(key)
        previous_phase: PRReviewPhase | None = None

        try:

            async def read_stderr():
                """Collect stderr for error reporting."""
                async for line_bytes in proc.stderr:
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                    if line:
                        stderr_lines.append(line)
                        logger.debug("[%s] STDERR: %s", sanitize_log(key), sanitize_log(line))

            # Start stderr reader in background
            stderr_task = asyncio.create_task(read_stderr())

            # Read stdout line by line
            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                logger.debug("[%s] %s", sanitize_log(key), sanitize_log(line))

                # Parse progress from runner output
                phase, progress, message = self._parse_progress(line)
                if phase:
                    # Track phase transitions for log writing
                    if previous_phase and previous_phase != phase:
                        if log_writer:
                            log_writer.complete_phase(previous_phase)
                            log_writer.start_phase(phase)

                    self._current_phases[key] = phase
                    previous_phase = phase

                    # Write to execution log
                    if log_writer:
                        log_writer.add_entry(phase, message, progress)

                    await self._emit_progress(
                        project_id,
                        pr_number,
                        phase,
                        message,
                        progress,
                    )

            # Wait for stderr reader to finish
            await stderr_task

            # Wait for process completion
            return_code = await proc.wait()

            if return_code == 0:
                # Finalize logs on success
                if log_writer:
                    final_phase = self._current_phases.get(key, PRReviewPhase.GENERATING)
                    log_writer.complete_phase(final_phase)
                    log_writer.finalize("completed")

                await self._emit_complete(project_id, pr_number, project_path)
            else:
                error_msg = (
                    "\n".join(stderr_lines[-5:])
                    if stderr_lines
                    else f"PR review failed with exit code {return_code}"
                )
                logger.error(
                    "PR review failed for %s: %s", sanitize_log(key), sanitize_log(error_msg)
                )

                # Finalize logs on failure
                if log_writer:
                    final_phase = self._current_phases.get(key, PRReviewPhase.STARTING)
                    log_writer.add_entry(final_phase, f"Failed: {error_msg}")
                    log_writer.complete_phase(final_phase, "failed")
                    log_writer.finalize("failed")

                await self._emit_error(project_id, pr_number, error_msg)

        except asyncio.CancelledError:
            logger.info("PR review cancelled for %s", sanitize_log(key))
            raise
        except Exception as e:  # noqa: BLE001
            # BLE001 exempts a blind except whose body calls logger.exception /
            # logger.error(exc_info=True), which this one used to do inline.
            # error_reference does exactly that logging, with the full traceback,
            # one frame deeper than ruff looks - so the exemption is lost even
            # though the handler is strictly more diligent than before.
            ref = error_reference(logger, f"process PR review output for #{pr_number}", e)

            # Finalize logs on unexpected error
            if log_writer:
                current_phase = self._current_phases.get(key, PRReviewPhase.STARTING)
                log_writer.add_entry(current_phase, f"Unexpected error (reference {ref})")
                log_writer.complete_phase(current_phase, "failed")
                log_writer.finalize("failed")

            await self._emit_error(
                project_id, pr_number, f"the review failed unexpectedly (reference {ref})"
            )
        finally:
            self._cleanup(key)

    def _parse_progress(self, line: str) -> tuple[PRReviewPhase | None, int, str]:
        """Parse a runner stdout line into phase, progress percentage, and message.

        Runner outputs lines like: [PR #123] [ 25%] Fetching PR data...
        """
        match = PROGRESS_PATTERN.match(line)
        if not match:
            return None, 0, ""

        progress = int(match.group(1))
        message = match.group(2).strip()

        # Map progress percentage ranges to phases
        if progress <= 10:
            phase = PRReviewPhase.FETCHING
        elif progress <= 50:
            phase = PRReviewPhase.ANALYZING
        elif progress < 100:
            phase = PRReviewPhase.GENERATING
        else:
            phase = PRReviewPhase.COMPLETE

        return phase, progress, message

    async def _emit_progress(
        self,
        project_id: str,
        pr_number: int,
        phase: PRReviewPhase,
        message: str,
        progress: int | None = None,
    ):
        """Emit progress event via WebSocket."""
        if progress is None:
            progress = PHASE_PROGRESS.get(phase, 0)

        logger.info(
            "[%s:PR#%s] Phase: %s (%s%%) - %s",
            sanitize_log(project_id),
            sanitize_log(pr_number),
            sanitize_log(phase.value),
            sanitize_log(progress),
            sanitize_log(message),
        )

        await broadcast_event(
            "pr:review-progress",
            {
                "projectId": project_id,
                "phase": phase.value,
                "prNumber": pr_number,
                "progress": progress,
                "message": message,
            },
        )

    async def _emit_complete(
        self,
        project_id: str,
        pr_number: int,
        project_path: Path,
    ):
        """Emit completion event via WebSocket.

        Reads stored review result from disk if available.
        """
        logger.info("[%s:PR#%s] Review complete", sanitize_log(project_id), sanitize_log(pr_number))

        # Try to read stored review result JSON from the project's .pfactory directory
        # Runner saves to: .pfactory/github/pr/review_{pr_number}.json
        result_data = None
        review_file = project_path / ".pfactory" / "github" / "pr" / f"review_{pr_number}.json"
        if review_file.exists():
            try:
                result_data = json.loads(review_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read review result: %s", sanitize_log(e))

        from .pr_data_service import _convert_keys

        await broadcast_event(
            "pr:review-complete",
            {
                "projectId": project_id,
                "prNumber": pr_number,
                "result": _convert_keys(result_data) if result_data else None,
            },
        )

    async def _emit_error(
        self,
        project_id: str,
        pr_number: int,
        error: str,
    ):
        """Emit error event via WebSocket."""
        logger.error(
            "[%s:PR#%s] Review error: %s",
            sanitize_log(project_id),
            sanitize_log(pr_number),
            sanitize_log(error),
        )

        await broadcast_event(
            "pr:review-error",
            {
                "projectId": project_id,
                "prNumber": pr_number,
                "error": error,
            },
        )

    def _cleanup(self, key: str):
        """Clean up tracking state for a review."""
        self.running_reviews.pop(key, None)
        self._current_phases.pop(key, None)
        self._log_writers.pop(key, None)
        self._review_start_times.pop(key, None)


# Singleton instance
_pr_review_service: PRReviewService | None = None


def get_pr_review_service() -> PRReviewService:
    """Get the singleton PRReviewService instance."""
    global _pr_review_service
    if _pr_review_service is None:
        _pr_review_service = PRReviewService()
    return _pr_review_service
