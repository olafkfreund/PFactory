"""
Changelog generation service.

Wraps the changelog_runner.py CLI as an async service with real-time progress streaming.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from factory_common.logsafe import sanitize_log
from server.background.tasks import spawn

from ..config import get_settings
from ..websockets.events import broadcast_event


class ChangelogPhase(str, Enum):
    """Changelog generation phases."""

    STARTING = "starting"
    LOADING_DATA = "loading_data"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    FORMATTING = "formatting"
    COMPLETE = "complete"
    FAILED = "failed"


# Phase progress percentages
PHASE_PROGRESS = {
    ChangelogPhase.STARTING: 0,
    ChangelogPhase.LOADING_DATA: 20,
    ChangelogPhase.ANALYZING: 40,
    ChangelogPhase.GENERATING: 60,
    ChangelogPhase.FORMATTING: 80,
    ChangelogPhase.COMPLETE: 100,
    ChangelogPhase.FAILED: 0,
}

# Pattern matching for phase detection from stdout
PHASE_PATTERNS = [
    (r"CHANGELOG PHASE 1.*STARTING", ChangelogPhase.STARTING),
    (r"CHANGELOG PHASE 2.*LOADING", ChangelogPhase.LOADING_DATA),
    (r"CHANGELOG PHASE 3.*ANALYZING", ChangelogPhase.ANALYZING),
    (r"CHANGELOG PHASE 4.*GENERATING", ChangelogPhase.GENERATING),
    (r"CHANGELOG PHASE 5.*FORMATTING", ChangelogPhase.FORMATTING),
    (r"CHANGELOG GENERATION COMPLETE", ChangelogPhase.COMPLETE),
    (r"CHANGELOG GENERATION FAILED", ChangelogPhase.FAILED),
]


@dataclass
class ChangelogProgress:
    """Changelog generation progress information."""

    project_id: str
    phase: ChangelogPhase
    progress: int
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


logger = logging.getLogger(__name__)


class ChangelogService:
    """Service for managing changelog generation."""

    def __init__(self):
        self.running_tasks: dict[str, asyncio.subprocess.Process] = {}
        self._current_phases: dict[str, ChangelogPhase] = {}

    def is_running(self, project_id: str) -> bool:
        """Check if changelog generation is running for a project."""
        return project_id in self.running_tasks

    def get_status(self, project_id: str) -> dict:
        """Get the current status for a project's changelog generation."""
        if project_id not in self.running_tasks:
            return {
                "isRunning": False,
                "status": "idle",
                "progress": 0,
                "message": None,
            }

        phase = self._current_phases.get(project_id, ChangelogPhase.STARTING)
        return {
            "isRunning": True,
            "status": phase.value,
            "progress": PHASE_PROGRESS.get(phase, 0),
            "message": f"Running: {phase.value.replace('_', ' ').title()}",
        }

    async def start_generation(
        self,
        project_id: str,
        project_path: Path,
        request: dict,
    ) -> bool:
        """Start changelog generation for a project."""
        if self.is_running(project_id):
            logger.warning(
                "Changelog generation already running for project %s", sanitize_log(project_id)
            )
            return False

        settings = get_settings()
        backend_path = Path(settings.BACKEND_PATH)
        changelog_runner = backend_path / "runners" / "changelog_runner.py"

        if not changelog_runner.exists():
            logger.error("changelog_runner.py not found at %s", sanitize_log(changelog_runner))
            await self._emit_error(project_id, "Changelog runner not found")
            return False

        # Use the web server's Python (which has shared dependencies)
        import sys

        python_path = sys.executable

        # Build command with request parameters
        cmd = [
            str(python_path),
            str(changelog_runner),
            "--project",
            str(project_path),
            "--source-mode",
            request.get("sourceMode", "tasks"),
            "--version",
            request.get("version", "1.0.0"),
            "--date",
            request.get("date", datetime.now().strftime("%Y-%m-%d")),
            "--format",
            request.get("format", "keep-a-changelog"),
            "--audience",
            request.get("audience", "user-facing"),
        ]

        # Add task IDs if present
        if request.get("taskIds"):
            cmd.extend(["--task-ids", ",".join(request["taskIds"])])

        # Add git history options if present
        if request.get("gitHistory"):
            git_history = request["gitHistory"]
            cmd.extend(["--git-history-type", git_history.get("type", "recent")])
            if git_history.get("count"):
                cmd.extend(["--git-history-count", str(git_history["count"])])
            if git_history.get("sinceDate"):
                cmd.extend(["--git-history-since-date", git_history["sinceDate"]])
            if git_history.get("fromTag"):
                cmd.extend(["--git-history-from-tag", git_history["fromTag"]])
            if git_history.get("toTag"):
                cmd.extend(["--git-history-to-tag", git_history["toTag"]])
            if git_history.get("includeMergeCommits"):
                cmd.append("--include-merge-commits")

        # Add branch diff options if present
        if request.get("branchDiff"):
            branch_diff = request["branchDiff"]
            # Use ref (git-resolvable) if provided, fall back to display name
            base = branch_diff.get("baseBranchRef") or branch_diff.get("baseBranch", "main")
            compare = branch_diff.get("compareBranchRef") or branch_diff.get(
                "compareBranch", "HEAD"
            )
            cmd.extend(["--base-branch", base])
            cmd.extend(["--compare-branch", compare])

        # Add emoji level if present
        if request.get("emojiLevel") and request["emojiLevel"] != "none":
            cmd.extend(["--emoji-level", request["emojiLevel"]])

        # Add custom instructions if present
        if request.get("customInstructions"):
            cmd.extend(["--custom-instructions", request["customInstructions"]])

        logger.info(
            "Starting changelog generation for %s: %s",
            sanitize_log(project_id),
            sanitize_log(" ".join(cmd)),
        )

        # Set up environment with PYTHONPATH pointing to backend.
        # Scrub ANTHROPIC_API_KEY (OAuth-only policy — see core/auth.py).
        from ..utils.subprocess_env import make_subprocess_env

        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Add backend path to PYTHONPATH so imports work
        existing_pythonpath = env.get("PYTHONPATH", "")
        backend_pythonpath = str(backend_path)
        runners_path = str(backend_path / "runners")
        if existing_pythonpath:
            env["PYTHONPATH"] = f"{backend_pythonpath}:{runners_path}:{existing_pythonpath}"
        else:
            env["PYTHONPATH"] = f"{backend_pythonpath}:{runners_path}"

        # Ensure OAuth token is passed through (required for Claude API)
        # Check backend .env file first, then settings
        backend_env_file = backend_path / ".env"
        if backend_env_file.exists():
            # Load token from backend .env if not already in environment
            if "CLAUDE_CODE_OAUTH_TOKEN" not in env:
                try:
                    with open(backend_env_file) as f:
                        for line in f:
                            if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                                if token:
                                    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
                                break
                except Exception as e:
                    logger.warning("Failed to read OAuth token from .env: %s", sanitize_log(e))

        try:
            # Start the subprocess - run from backend directory
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(backend_path),
                env=env,
            )

            self.running_tasks[project_id] = proc
            self._current_phases[project_id] = ChangelogPhase.STARTING

            # Emit initial progress
            await self._emit_progress(
                project_id, ChangelogPhase.STARTING, "Starting changelog generation..."
            )

            # Start output processing in background
            spawn(self._process_output(project_id, project_path, proc))

            return True

        except Exception as e:
            logger.error("Failed to start changelog generation: %s", sanitize_log(e))
            await self._emit_error(project_id, str(e))
            return False

    async def stop_generation(self, project_id: str) -> bool:
        """Stop changelog generation for a project."""
        if not self.is_running(project_id):
            return False

        proc = self.running_tasks.get(project_id)
        if proc:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
            except Exception as e:
                logger.error("Error stopping changelog generation: %s", sanitize_log(e))

        self._cleanup(project_id)
        await self._emit_stopped(project_id)
        return True

    async def _process_output(
        self,
        project_id: str,
        project_path: Path,
        proc: asyncio.subprocess.Process,
    ):
        """Process subprocess output and emit progress events."""
        stderr_lines = []

        try:
            # Read stdout and stderr concurrently
            async def read_stderr():
                """Collect stderr for error reporting."""
                async for line_bytes in proc.stderr:
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                    if line:
                        stderr_lines.append(line)
                        logger.error(
                            "[%s] STDERR: %s", sanitize_log(project_id), sanitize_log(line)
                        )

            # Start stderr reader in background
            stderr_task = asyncio.create_task(read_stderr())

            # Read stdout line by line
            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                logger.debug("[%s] %s", sanitize_log(project_id), sanitize_log(line))

                # Detect phase changes
                phase = self._detect_phase(line)
                if phase:
                    self._current_phases[project_id] = phase
                    message = line.strip()
                    await self._emit_progress(project_id, phase, message)

            # Wait for stderr reader to finish
            await stderr_task

            # Wait for process completion
            return_code = await proc.wait()

            if return_code == 0:
                # Success - emit completion with generated content
                await self._emit_complete(project_id, project_path)
            else:
                # Failure - emit error with stderr
                error_msg = (
                    "\n".join(stderr_lines)
                    if stderr_lines
                    else f"Changelog generation failed with exit code {return_code}"
                )
                logger.error("Changelog generation failed: %s", sanitize_log(error_msg))
                await self._emit_error(project_id, error_msg)

        except asyncio.CancelledError:
            logger.info("Changelog generation cancelled for %s", sanitize_log(project_id))
            raise
        except Exception as e:
            logger.error("Error processing changelog output: %s", sanitize_log(e), exc_info=True)
            await self._emit_error(project_id, f"Unexpected error: {e!s}")
        finally:
            self._cleanup(project_id)

    def _detect_phase(self, line: str) -> ChangelogPhase | None:
        """Detect phase from stdout line using regex patterns."""
        for pattern, phase in PHASE_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                return phase
        return None

    async def _emit_progress(self, project_id: str, phase: ChangelogPhase, message: str):
        """Emit progress event via WebSocket."""
        progress = PHASE_PROGRESS.get(phase, 0)
        logger.info(
            "[%s] Phase: %s (%s%%) - %s",
            sanitize_log(project_id),
            sanitize_log(phase.value),
            sanitize_log(progress),
            sanitize_log(message),
        )

        await broadcast_event(
            "changelog:progress",
            {
                "projectId": project_id,
                "phase": phase.value,
                "progress": progress,
                "message": message,
            },
        )

    async def _emit_complete(self, project_id: str, project_path: Path):
        """Load generated file and emit completion event."""
        try:
            # Read the generated changelog
            changelog_path = project_path / ".pfactory" / "changelog" / "generated.md"
            if changelog_path.exists():
                content = changelog_path.read_text(encoding="utf-8")
            else:
                content = "# Changelog\n\nNo changelog content was generated."
                logger.warning(
                    "Generated changelog file not found at %s", sanitize_log(changelog_path)
                )

            logger.info("[%s] Changelog generation complete", sanitize_log(project_id))

            await broadcast_event(
                "changelog:complete",
                {
                    "projectId": project_id,
                    "success": True,
                    "changelog": content,
                    "version": "generated",  # Could extract from content if needed
                    "tasksIncluded": 0,  # Could track this if needed
                },
            )

        except Exception as e:
            logger.error("Error reading generated changelog: %s", sanitize_log(e))
            await self._emit_error(project_id, f"Failed to read generated changelog: {e!s}")

    async def _emit_error(self, project_id: str, error: str):
        """Emit error event."""
        logger.error("[%s] Error: %s", sanitize_log(project_id), sanitize_log(error))

        await broadcast_event(
            "changelog:error",
            {
                "projectId": project_id,
                "error": error,
                "phase": self._current_phases.get(project_id, ChangelogPhase.FAILED).value,
            },
        )

    async def _emit_stopped(self, project_id: str):
        """Emit stopped event."""
        logger.info("[%s] Generation stopped", sanitize_log(project_id))

        await broadcast_event("changelog:stopped", {"projectId": project_id})

    def _cleanup(self, project_id: str):
        """Clean up process references."""
        self.running_tasks.pop(project_id, None)
        self._current_phases.pop(project_id, None)


# Singleton instance
_changelog_service: ChangelogService | None = None


def get_changelog_service() -> ChangelogService:
    """Get the global changelog service instance."""
    global _changelog_service
    if _changelog_service is None:
        _changelog_service = ChangelogService()
    return _changelog_service
