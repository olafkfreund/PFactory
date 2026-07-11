"""
Claude token/profile failover mixin for AgentService.

Token resolution, rate-limit detection, profile switching, and subprocess
retry with an alternate profile or fallback model. Extracted verbatim from
``agent_service.py`` (issue Factory#255 seam c); ``AgentService`` inherits
this mixin, so behavior is unchanged.

The per-line ``noqa`` directives mark pre-existing violations carried over
verbatim (behavior-preserving move; legacy is fixed on touch per the ratchet
policy, not during extraction).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings  # noqa: TID252


class AgentFailoverMixin:
    """Token/profile failover behavior mixed into ``AgentService``.

    Host-class dependencies (attributes defined in ``AgentService.__init__``)
    are declared here for type checkers only.
    """

    if TYPE_CHECKING:
        settings: Settings
        _task_profiles: dict[str, dict[str, Any]]

    def _resolve_claude_token(self, exclude_profile_id: str | None = None) -> tuple[str | None, str | None, str | None]:  # noqa: E501
        """Resolve Claude OAuth token from profiles with fallback chain.

        Resolution order:
        1. Environment override (CLAUDE_CODE_OAUTH_TOKEN already set)
        2. Active profile from ~/.pfactory/claude-profiles.json
        3. Best available profile (excluding failed profile if provided)
        4. Fallback to ~/.claude/oauth_token

        Args:
            exclude_profile_id: Profile ID to exclude (for retry after failure)

        Returns:
            Tuple of (token, profile_id, profile_name) or (None, None, None) if no token found
        """
        import logging  # noqa: PLC0415
        logger = logging.getLogger(__name__)

        # Check environment override first
        if "CLAUDE_CODE_OAUTH_TOKEN" in os.environ:
            # Allow failover when this "env-override" profile is excluded.
            if exclude_profile_id != "env-override":
                logger.info("[AgentService] Using CLAUDE_CODE_OAUTH_TOKEN from environment")
                return (os.environ["CLAUDE_CODE_OAUTH_TOKEN"], "env-override", "Environment Override")  # noqa: E501
            logger.info("[AgentService] Skipping environment token due to exclude_profile_id=env-override (failover enabled)")  # noqa: E501

        # Load claude-profiles.json
        profiles_file = Path(self.settings.PROJECTS_DATA_DIR) / "claude-profiles.json"
        from ..paths import get_data_file  # noqa: PLC0415, TID252
        legacy_profiles_file = get_data_file("claude-profiles.json")
        if not profiles_file.exists() and legacy_profiles_file.exists():
            profiles_file = legacy_profiles_file
            logger.debug(f"[AgentService] Using legacy profiles file at {profiles_file}")

        if profiles_file.exists():
            try:
                data = json.loads(profiles_file.read_text())
                profiles = data.get("profiles", [])
                active_id = data.get("activeProfileId")

                # Filter usable profiles (has token, not excluded)
                usable = [
                    p for p in profiles
                    if p.get("id") != exclude_profile_id
                    and (p.get("oauthToken") or p.get("token"))  # Support both field names
                ]

                if usable:
                    # Prefer active profile if it's usable
                    for p in usable:
                        if p.get("id") == active_id:
                            token = p.get("oauthToken") or p.get("token")
                            profile_id = p.get("id")
                            profile_name = p.get("name", "Active Profile")
                            logger.info(f"[AgentService] Using active profile: {profile_name} ({profile_id})")  # noqa: E501
                            return (token, profile_id, profile_name)

                    # Use first usable profile
                    p = usable[0]
                    token = p.get("oauthToken") or p.get("token")
                    profile_id = p.get("id")
                    profile_name = p.get("name", "Default Profile")
                    logger.info(f"[AgentService] Using profile: {profile_name} ({profile_id})")
                    return (token, profile_id, profile_name)

            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[AgentService] Failed to load claude-profiles.json: {e}")

        # Fallback to static token file
        token_file = Path.home() / ".claude" / "oauth_token"
        if token_file.exists():
            token = token_file.read_text().strip()
            logger.info("[AgentService] Using fallback token from ~/.claude/oauth_token")
            return (token, "static-fallback", "Static Token")

        logger.warning("[AgentService] No Claude token found")
        return (None, None, None)

    def _is_early_failure(self, spec_dir: Path, exit_code: int) -> bool:
        """Check if task failure is an early failure (no logs written).

        Early failure criteria:
        - Exit code is non-zero
        - task_logs.json either doesn't exist OR has no entries in any phase

        This indicates the agent failed immediately without making progress,
        typically due to auth/rate-limit issues.

        Args:
            spec_dir: Path to the spec directory containing task_logs.json
            exit_code: Process exit code

        Returns:
            True if this is an early failure eligible for retry
        """
        if exit_code == 0:
            return False

        task_logs_file = spec_dir / "task_logs.json"

        # If file doesn't exist, it's an early failure
        if not task_logs_file.exists():
            return True

        try:
            data = json.loads(task_logs_file.read_text())
            phases = data.get("phases", {})

            # Check if any phase has entries
            for phase_name, phase_data in phases.items():  # noqa: B007
                entries = phase_data.get("entries", [])
                if entries:
                    # Found entries - this is NOT an early failure
                    return False

            # No entries in any phase - early failure
            return True

        except (json.JSONDecodeError, OSError):
            # Can't read logs - assume early failure to be safe
            return True

    def _should_retry_with_failover(self) -> bool:
        """Check if auto-switch settings allow profile failover.

        Checks:
        - enabled: Master switch for auto-switching
        - autoSwitchOnRateLimit: Reactive recovery toggle

        Returns:
            True if both settings are enabled
        """
        import logging  # noqa: PLC0415
        logger = logging.getLogger(__name__)

        # Primary path: PROJECTS_DATA_DIR/auto-switch.json
        settings_file = Path(self.settings.PROJECTS_DATA_DIR) / "auto-switch.json"
        # Legacy path: ~/.pfactory/data/auto-switch.json. Resolved via Path.home()
        # at call time (not server.paths.get_data_file, whose AI_FACTORY_DIR
        # constant is frozen at import time and wouldn't honor a mocked home
        # directory in tests) so callers/tests can override the home dir.
        legacy_settings_file = Path.home() / ".pfactory" / "data" / "auto-switch.json"
        if not settings_file.exists() and legacy_settings_file.exists():
            settings_file = legacy_settings_file
            logger.debug(f"[AgentService] Using legacy auto-switch settings file at {settings_file}")  # noqa: E501

        if not settings_file.exists():
            logger.debug(f"[AgentService] Auto-switch settings not found at {settings_file}, failover disabled")  # noqa: E501
            return False

        try:
            data = json.loads(settings_file.read_text())
            enabled = data.get("enabled", False)
            auto_switch_on_rate_limit = data.get("autoSwitchOnRateLimit", False)

            if enabled and auto_switch_on_rate_limit:
                logger.info("[AgentService] Auto-switch enabled - failover allowed")
                return True
            else:
                logger.debug(f"[AgentService] Auto-switch disabled - enabled: {enabled}, autoSwitchOnRateLimit: {auto_switch_on_rate_limit}")  # noqa: E501
                return False

        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[AgentService] Failed to read auto-switch settings: {e}")
            return False

    def _is_rate_limit_line(self, line: str) -> bool:
        """Detect rate limit messages in agent output."""
        text = line.lower()
        patterns = [
            "you've hit your limit",
            "you’ve hit your limit",  # curly apostrophe  # noqa: RUF001
            "youve hit your limit",
        ]
        return any(p in text for p in patterns)

    async def _emit_profile_switch(
        self,
        task_id: str,
        old_profile_id: str,
        new_profile_id: str,
        new_profile_name: str,
        reason: str
    ) -> None:
        """Emit profile switch event via WebSocket.

        Args:
            task_id: Task identifier
            old_profile_id: Previous profile ID that failed
            new_profile_id: New profile ID being used
            new_profile_name: New profile display name
            reason: Reason for switch (e.g., "early_failure")
        """
        from ..websockets.events import broadcast_event  # noqa: PLC0415, TID252

        await broadcast_event("task:profile-switch", {
            "taskId": task_id,
            "oldProfileId": old_profile_id,
            "newProfileId": new_profile_id,
            "newProfileName": new_profile_name,
            "reason": reason,
            "timestamp": datetime.now().isoformat()  # noqa: DTZ005
        })

    def _update_active_profile(self, profile_id: str, profile_name: str, reason: str = "rate_limit") -> None:  # noqa: E501
        """Update active profile system-wide when reactive failover occurs.

        This updates the activeProfileId in claude-profiles.json so that all future
        tasks automatically use the new profile instead of repeatedly failing.

        Args:
            profile_id: ID of new profile to make active
            profile_name: Name for logging
            reason: Why the switch occurred (e.g., "rate_limit", "reactive_failover")
        """
        import logging  # noqa: PLC0415
        logger = logging.getLogger(__name__)

        profiles_file = Path(self.settings.PROJECTS_DATA_DIR) / "claude-profiles.json"
        from ..paths import get_data_file  # noqa: PLC0415, TID252
        legacy_profiles_file = get_data_file("claude-profiles.json")

        if not profiles_file.exists() and legacy_profiles_file.exists():
            profiles_file = legacy_profiles_file
            logger.debug(f"[AgentService] Using legacy profiles file at {profiles_file}")

        if not profiles_file.exists():
            logger.warning("[AgentService] claude-profiles.json not found, skipping active profile update")  # noqa: E501
            return

        try:
            # Read current profiles
            data = json.loads(profiles_file.read_text())
            old_active = data.get("activeProfileId")

            # Update active profile
            data["activeProfileId"] = profile_id

            # Write back with secure permissions
            profiles_file.write_text(json.dumps(data, indent=2))
            profiles_file.chmod(0o600)

            # Update env token to match active profile (if available)
            token = None
            for profile in data.get("profiles", []):
                if profile.get("id") == profile_id:
                    token = profile.get("oauthToken") or profile.get("token")
                    break

            if token:
                os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
                logger.info("[AgentService] Updated CLAUDE_CODE_OAUTH_TOKEN for active profile")
            else:
                logger.warning("[AgentService] Active profile has no token; env not updated")

            logger.info(f"[AgentService] Updated active profile: {old_active} → {profile_id} (reason: {reason})")  # noqa: E501

            # Emit WebSocket event for system-wide profile change
            from ..websockets.events import broadcast_event  # noqa: PLC0415, TID252
            asyncio.create_task(broadcast_event("profile:changed", {  # noqa: RUF006
                "oldProfileId": old_active,
                "newProfileId": profile_id,
                "newProfileName": profile_name,
                "reason": reason,
                "timestamp": datetime.now().isoformat()  # noqa: DTZ005
            }))

        except Exception as e:  # noqa: BLE001
            logger.error(f"[AgentService] Failed to update active profile: {e}")

    async def _retry_task_with_fallback_model(
        self,
        task_id: str,
        project_path: Path,
        spec_id: str,  # noqa: ARG002
        cmd: list[str],
        env: dict[str, str],
    ) -> asyncio.subprocess.Process | None:
        """Retry task execution with Claude Sonnet as fallback model.

        Called when a non-Claude model (Codex, Gemini, Ollama) fails.
        Swaps the --model flag in the command to 'sonnet'.

        Returns:
            New subprocess or None if retry not possible
        """
        import logging  # noqa: PLC0415
        logger = logging.getLogger(__name__)

        profile_info = self._task_profiles.get(task_id, {})
        failed_model = profile_info.get("model", "unknown")

        # Build new command with sonnet model
        new_cmd = list(cmd)
        if "--model" in new_cmd:
            model_idx = new_cmd.index("--model")
            if model_idx + 1 < len(new_cmd):
                new_cmd[model_idx + 1] = "sonnet"
        else:
            new_cmd.extend(["--model", "sonnet"])

        logger.info(f"[AgentService] [Model: sonnet] Fallback triggered for {task_id} (original: {failed_model})")  # noqa: E501

        # Emit WebSocket event for model fallback
        from ..websockets.events import broadcast_event  # noqa: PLC0415, TID252
        await broadcast_event("task:log", {
            "taskId": task_id,
            "type": "model_fallback",
            "message": f"Model '{failed_model}' failed. Falling back to Claude Sonnet.",
        })

        # Update tracking
        if task_id in self._task_profiles:
            self._task_profiles[task_id]["model"] = "sonnet"
            self._task_profiles[task_id]["attempt"] = 2
            self._task_profiles[task_id]["fallbackFrom"] = failed_model

        # Relaunch subprocess
        import pty  # noqa: PLC0415
        master_fd, slave_fd = pty.openpty()

        proc = await asyncio.create_subprocess_exec(
            *new_cmd,
            stdin=slave_fd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
            env=env,
        )

        os.close(slave_fd)
        os.close(master_fd)

        return proc

    async def _retry_task_with_profile(  # noqa: PLR0913
        self,
        task_id: str,
        project_path: Path,
        spec_id: str,  # noqa: ARG002
        cmd: list[str],
        env: dict[str, str],
        failed_profile_id: str,
        reason: str,
    ) -> asyncio.subprocess.Process | None:
        """Retry task execution with a different Claude profile.

        Args:
            task_id: Task identifier
            project_path: Project directory
            spec_id: Spec identifier
            cmd: Command to execute (same as original)
            env: Environment dict (will update token)
            failed_profile_id: Profile ID that failed (to exclude)

        Returns:
            New subprocess or None if retry not possible
        """
        import logging  # noqa: PLC0415
        logger = logging.getLogger(__name__)

        # Resolve alternate token (excluding failed profile)
        token, profile_id, profile_name = self._resolve_claude_token(exclude_profile_id=failed_profile_id)  # noqa: E501

        if not token:
            logger.warning(f"[AgentService] No alternate profile available for retry (excluded: {failed_profile_id})")  # noqa: E501
            return None

        if profile_id == failed_profile_id:
            logger.warning(f"[AgentService] Only profile available is the one that failed ({failed_profile_id})")  # noqa: E501
            return None

        # Update environment with new token
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token

        # Log profile switch
        logger.info(f"[AgentService] Retrying with profile: {profile_name} ({profile_id})")

        # Emit WebSocket event for profile switch
        await self._emit_profile_switch(
            task_id=task_id,
            old_profile_id=failed_profile_id,
            new_profile_id=profile_id,  # type: ignore[arg-type]
            new_profile_name=profile_name,  # type: ignore[arg-type]
            reason=reason,
        )

        # Update active profile system-wide (only for rate limit, not early failure)
        if reason == "rate_limit":
            self._update_active_profile(profile_id, profile_name, reason="reactive_failover")  # type: ignore[arg-type]

        # Update tracking
        if task_id in self._task_profiles:
            self._task_profiles[task_id] = {
                "profileId": profile_id,
                "profileName": profile_name,
                "attempt": 2,  # Second attempt
                "previousProfileId": failed_profile_id
            }

        # Relaunch subprocess with new token
        import pty  # noqa: PLC0415
        master_fd, slave_fd = pty.openpty()  # noqa: RUF059

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=slave_fd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(project_path),
            env=env,
        )

        os.close(slave_fd)

        return proc
