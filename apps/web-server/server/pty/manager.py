"""
PTY Manager - Manages multiple terminal sessions.

Provides session lifecycle management, persistence, and lookup.
"""

import json
import logging
import os
from pathlib import Path
from threading import Lock

from ..config import get_settings
from .session import PTYSession


class PTYManager:
    """Manages multiple PTY sessions."""

    def __init__(self):
        self.sessions: dict[str, PTYSession] = {}
        self._lock = Lock()
        self._max_sessions = get_settings().MAX_TERMINALS

    def create_session(
        self,
        cwd: str | None = None,
        shell: str | None = None,
        cols: int = 80,
        rows: int = 24,
        session_id: str | None = None,
    ) -> PTYSession:
        """Create a new PTY session."""
        logger = logging.getLogger(__name__)
        token_env: dict[str, str] = {}
        token, profile_id, profile_name = self._resolve_claude_token()
        if token:
            token_env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(
                f"[PTYManager] Using Claude profile for terminal: {profile_name} ({profile_id})"
            )
        else:
            logger.warning("[PTYManager] No Claude OAuth token available for terminal")

        with self._lock:
            # Check session limit
            if len(self.sessions) >= self._max_sessions:
                raise RuntimeError(f"Maximum sessions ({self._max_sessions}) reached")

            # Create session - only pass id if provided, otherwise let dataclass generate UUID
            session_kwargs = {
                "cwd": cwd or str(Path.home()),
                "shell": shell or get_settings().DEFAULT_SHELL,
                "cols": cols,
                "rows": rows,
                "env": token_env or None,
            }
            if session_id:
                session_kwargs["id"] = session_id

            session = PTYSession(**session_kwargs)

            # Start the PTY
            session.start()

            # Store session
            self.sessions[session.id] = session

            return session

    def _resolve_claude_token(self) -> tuple[str | None, str | None, str | None]:
        """Resolve Claude OAuth token with profile-aware fallback."""
        # 1) Environment override
        env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if env_token:
            return (env_token, "env-override", "Environment Override")

        # 2) Active profile from profiles file
        settings = get_settings()
        profiles_file = Path(settings.PROJECTS_DATA_DIR) / "claude-profiles.json"
        from ..paths import get_data_file
        legacy_profiles_file = get_data_file("claude-profiles.json")
        if not profiles_file.exists() and legacy_profiles_file.exists():
            profiles_file = legacy_profiles_file

        if profiles_file.exists():
            try:
                data = json.loads(profiles_file.read_text())
                profiles = data.get("profiles", [])
                active_id = data.get("activeProfileId")

                usable = [
                    p for p in profiles
                    if p.get("oauthToken") or p.get("token")
                ]

                for profile in usable:
                    if profile.get("id") == active_id:
                        token = profile.get("oauthToken") or profile.get("token")
                        return (token, profile.get("id"), profile.get("name", "Active Profile"))

                if usable:
                    profile = usable[0]
                    token = profile.get("oauthToken") or profile.get("token")
                    return (token, profile.get("id"), profile.get("name", "Default Profile"))
            except (json.JSONDecodeError, OSError):
                pass

        # 3) Fallback to ~/.claude/oauth_token
        token_file = Path.home() / ".claude" / "oauth_token"
        if token_file.exists():
            token = token_file.read_text().strip()
            if token:
                return (token, "static-fallback", "Static Token")

        return (None, None, None)

    def get_session(self, session_id: str) -> PTYSession | None:
        """Get a session by ID."""
        return self.sessions.get(session_id)

    def close_session(self, session_id: str) -> bool:
        """Close and remove a session."""
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None:
                return False

            session.close()
            del self.sessions[session_id]
            return True

    def list_sessions(self) -> list[dict]:
        """List all active sessions."""
        return [session.to_dict() for session in self.sessions.values()]

    def get_session_count(self) -> int:
        """Get number of active sessions."""
        return len(self.sessions)

    def close_all(self) -> int:
        """Close all sessions. Returns number of sessions closed."""
        with self._lock:
            count = len(self.sessions)
            for session in list(self.sessions.values()):
                session.close()
            self.sessions.clear()
            return count

    def cleanup_dead_sessions(self) -> int:
        """Remove sessions whose PTY processes have exited."""
        with self._lock:
            dead_sessions = [
                sid for sid, session in self.sessions.items()
                if not session.is_alive()
            ]
            for sid in dead_sessions:
                del self.sessions[sid]
            return len(dead_sessions)


# Global manager instance
_pty_manager: PTYManager | None = None


def get_pty_manager() -> PTYManager:
    """Get the global PTY manager instance."""
    global _pty_manager
    if _pty_manager is None:
        _pty_manager = PTYManager()
    return _pty_manager
