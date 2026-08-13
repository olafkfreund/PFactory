"""
Insights AI Chat Service.

Provides AI-powered chat for codebase exploration using multiple LLM providers.
Streams responses via WebSocket and persists sessions to disk.
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from factory_common.logsafe import sanitize_log

from ..websockets.events import broadcast_event
from .git_utils import safe_spec_component  # #335
from .insights_providers import get_provider

logger = logging.getLogger(__name__)


def _parse_task_json(raw: str) -> dict:
    """Parse a JSON task object from LLM output.

    Tries, in order:
      1. Strip markdown fences and json.loads the whole string
      2. Brace-matching extraction
      3. Fallback: use the raw text as the description
    """
    # Strip markdown fences (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

    # Attempt 1: direct parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return {
                "title": str(parsed.get("title", "")).strip(),
                "description": str(parsed.get("description", "")).strip(),
            }
    except json.JSONDecodeError:
        pass

    # Attempt 2: brace-matching — find first { … }
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(cleaned[start : i + 1])
                        if isinstance(parsed, dict):
                            return {
                                "title": str(parsed.get("title", "")).strip(),
                                "description": str(parsed.get("description", "")).strip(),
                            }
                    except json.JSONDecodeError:
                        break

    # Attempt 3: use raw text as description
    return {"title": "", "description": cleaned.strip()}


@dataclass
class InsightsMessage:
    """A single chat message."""

    id: str
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    suggested_task: dict | None = None
    tools_used: list | None = None
    provider: str | None = None  # e.g. 'claude', 'ollama', 'codex'
    provider_model: str | None = None  # e.g. 'opus', 'llama3:8b'


@dataclass
class InsightsSession:
    """A chat session with history."""

    id: str
    project_id: str
    title: str
    messages: list[InsightsMessage] = field(default_factory=list)
    model_config: dict | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class InsightsService:
    """Service for AI-powered insights chat."""

    def __init__(self):
        self._running_tasks: dict[str, asyncio.Task] = {}  # projectId -> running asyncio task
        self._sessions: dict[str, InsightsSession] = {}  # Cache

    def _get_sessions_dir(self, project_path: Path) -> Path:
        """Get the directory for storing insight sessions."""
        sessions_dir = project_path / ".pfactory" / "insights"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir

    def _get_session_file(self, project_path: Path, session_id: str) -> Path:
        """Get the file path for a specific session."""
        session_id = safe_spec_component(session_id, field="session_id")  # #335
        return self._get_sessions_dir(project_path) / f"{session_id}.json"

    def _get_current_session_file(self, project_path: Path) -> Path:
        """Get the file that tracks the current active session."""
        return self._get_sessions_dir(project_path) / "current_session.txt"

    def _save_session(self, project_path: Path, session: InsightsSession) -> None:
        """Save a session to disk."""
        session.updated_at = datetime.now().isoformat()
        session_file = self._get_session_file(project_path, session.id)

        data = {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "suggestedTask": msg.suggested_task,
                    "toolsUsed": msg.tools_used,
                    "provider": msg.provider,
                    "providerModel": msg.provider_model,
                }
                for msg in session.messages
            ],
            "modelConfig": session.model_config,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        }

        with open(session_file, "w") as f:
            json.dump(data, f, indent=2)

        self._sessions[session.id] = session

    def _load_session(self, project_path: Path, session_id: str) -> InsightsSession | None:
        """Load a session from disk."""
        if session_id in self._sessions:
            return self._sessions[session_id]

        session_file = self._get_session_file(project_path, session_id)
        if not session_file.exists():
            return None

        try:
            with open(session_file) as f:
                data = json.load(f)

            session = InsightsSession(
                id=data["id"],
                project_id=data.get("projectId", ""),
                title=data.get("title", "New Session"),
                messages=[
                    InsightsMessage(
                        id=msg["id"],
                        role=msg["role"],
                        content=msg["content"],
                        timestamp=msg["timestamp"],
                        suggested_task=msg.get("suggestedTask"),
                        tools_used=msg.get("toolsUsed"),
                        provider=msg.get("provider"),
                        provider_model=msg.get("providerModel"),
                    )
                    for msg in data.get("messages", [])
                ],
                model_config=data.get("modelConfig"),
                created_at=data.get("createdAt", datetime.now().isoformat()),
                updated_at=data.get("updatedAt", datetime.now().isoformat()),
            )

            self._sessions[session_id] = session
            return session
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                "Failed to load session %s: %s", sanitize_log(session_id), sanitize_log(e)
            )
            return None

    def get_current_session(self, project_path: Path, project_id: str) -> InsightsSession:
        """Get or create the current session for a project."""
        current_file = self._get_current_session_file(project_path)

        if current_file.exists():
            session_id = current_file.read_text().strip()
            session = self._load_session(project_path, session_id)
            if session:
                return session

        return self.create_session(project_path, project_id)

    # Default model config for new sessions
    DEFAULT_MODEL_CONFIG = {
        "provider": "claude",
        "model": "sonnet",
        "thinkingLevel": "medium",
    }

    def create_session(self, project_path: Path, project_id: str) -> InsightsSession:
        """Create a new session."""
        session = InsightsSession(
            id=str(uuid.uuid4()),
            project_id=project_id,
            title="New Session",
            model_config=dict(self.DEFAULT_MODEL_CONFIG),
        )

        self._save_session(project_path, session)

        current_file = self._get_current_session_file(project_path)
        current_file.write_text(session.id)

        return session

    def switch_session(self, project_path: Path, session_id: str) -> InsightsSession | None:
        """Switch to a different session."""
        session = self._load_session(project_path, session_id)
        if session:
            current_file = self._get_current_session_file(project_path)
            current_file.write_text(session_id)
        return session

    def list_sessions(self, project_path: Path) -> list[dict]:
        """List all sessions for a project."""
        sessions_dir = self._get_sessions_dir(project_path)
        sessions = []

        for session_file in sessions_dir.glob("*.json"):
            if session_file.name == "current_session.txt":
                continue
            try:
                with open(session_file) as f:
                    data = json.load(f)
                sessions.append(
                    {
                        "id": data["id"],
                        "title": data.get("title", "Untitled"),
                        "messageCount": len(data.get("messages", [])),
                        "createdAt": data.get("createdAt"),
                        "updatedAt": data.get("updatedAt"),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue

        sessions.sort(key=lambda x: x.get("updatedAt", ""), reverse=True)
        return sessions

    def delete_session(self, project_path: Path, session_id: str) -> dict:
        """Delete a session. Returns info about what happened."""
        session_file = self._get_session_file(project_path, session_id)
        if not session_file.exists():
            return {"deleted": False}

        session_file.unlink()

        if session_id in self._sessions:
            del self._sessions[session_id]

        was_current = False
        current_file = self._get_current_session_file(project_path)
        if current_file.exists() and current_file.read_text().strip() == session_id:
            current_file.unlink()
            was_current = True

        # If the deleted session was the current one, switch to the most recent remaining session
        switched_to = None
        if was_current:
            remaining = self.list_sessions(project_path)
            if remaining:
                # Switch to the most recent session (list is already sorted by updatedAt desc)
                next_session_id = remaining[0]["id"]
                current_file.write_text(next_session_id)
                switched_to = next_session_id

        return {"deleted": True, "switchedTo": switched_to}

    def rename_session(self, project_path: Path, session_id: str, new_title: str) -> bool:
        """Rename a session."""
        session = self._load_session(project_path, session_id)
        if session:
            session.title = new_title
            self._save_session(project_path, session)
            return True
        return False

    def update_model_config(self, project_path: Path, session_id: str, model_config: dict) -> bool:
        """Update model config for a session."""
        session = self._load_session(project_path, session_id)
        if session:
            session.model_config = model_config
            self._save_session(project_path, session)
            return True
        return False

    async def send_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model_config: dict | None = None,
    ) -> None:
        """Send a message and stream the response via the appropriate provider."""
        # Get current session
        session = self.get_current_session(project_path, project_id)

        # Merge session config with message-level config (message takes precedence)
        effective_config = dict(self.DEFAULT_MODEL_CONFIG)
        if session.model_config:
            effective_config.update(
                {k: v for k, v in session.model_config.items() if v is not None}
            )
        if model_config:
            effective_config.update({k: v for k, v in model_config.items() if v is not None})
        model_config = effective_config

        # Determine provider from model_config (default: claude)
        provider_id = model_config.get("provider", "claude")
        provider_model = model_config.get("model", "sonnet")

        # Add user message
        user_msg = InsightsMessage(
            id=f"msg-{uuid.uuid4().hex[:8]}",
            role="user",
            content=message,
            timestamp=datetime.now().isoformat(),
        )
        session.messages.append(user_msg)

        # Auto-generate title from first message
        if session.title == "New Session" and len(session.messages) == 1:
            session.title = message[:50] + ("..." if len(message) > 50 else "")

        self._save_session(project_path, session)

        # Build conversation history for stateless providers
        conversation_history = [
            {"role": msg.role, "content": msg.content}
            for msg in session.messages[:-1]  # Exclude current user message
        ]

        # Route to provider
        provider = get_provider(provider_id)
        logger.info(
            "[InsightsService] Routing to provider: %s (model: %s)",
            sanitize_log(provider_id),
            sanitize_log(provider_model),
        )

        try:
            response_content = await provider.send_message(
                project_path=project_path,
                project_id=project_id,
                message=message,
                model=provider_model,
                model_config=model_config,
                conversation_history=conversation_history if provider_id != "claude" else None,
            )

            # Persist the assistant response to disk
            if response_content and response_content.strip():
                assistant_msg = InsightsMessage(
                    id=f"msg-{uuid.uuid4().hex[:8]}",
                    role="assistant",
                    content=response_content,
                    timestamp=datetime.now().isoformat(),
                    provider=provider_id,
                    provider_model=provider_model,
                )
                session.messages.append(assistant_msg)
                self._save_session(project_path, session)

        except asyncio.CancelledError:
            logger.info("[InsightsService] Chat cancelled for project %s", sanitize_log(project_id))
            # Finalize partial content if any
            await broadcast_event(
                "insights:chunk",
                {
                    "projectId": project_id,
                    "type": "done",
                },
            )
        except Exception as e:
            logger.error("[InsightsService] Provider error: %s", sanitize_log(e), exc_info=True)
            await broadcast_event(
                "insights:chunk",
                {
                    "projectId": project_id,
                    "type": "error",
                    "error": str(e),
                },
            )
        finally:
            self._running_tasks.pop(project_id, None)

    def start_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model_config: dict | None = None,
    ) -> None:
        """Start send_message as a tracked background task."""
        # Cancel any existing running task for this project
        self.stop_message(project_id)

        task = asyncio.create_task(
            self.send_message(project_path, project_id, message, model_config)
        )
        self._running_tasks[project_id] = task

    def stop_message(self, project_id: str) -> bool:
        """Cancel the running chat task for a project. Returns True if a task was cancelled."""
        task = self._running_tasks.pop(project_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(
                "[InsightsService] Cancelled running task for project %s", sanitize_log(project_id)
            )
            return True
        return False

    async def generate_task_from_chat(
        self,
        project_path: Path,
        project_id: str,
        model_config: dict | None = None,
    ) -> dict:
        """Summarize the current chat session into a structured task.

        Runs a lightweight ``claude --print`` call (no tool use,
        no streaming) to produce a JSON ``{title, description}`` object.
        """
        import os
        import shutil

        session = self.get_current_session(project_path, project_id)
        if not session or not session.messages:
            return {"title": "", "description": ""}

        # Build transcript
        transcript_lines: list[str] = []
        for msg in session.messages:
            role = "User" if msg.role == "user" else "Assistant"
            transcript_lines.append(f"[{role}]: {msg.content}")
        transcript = "\n\n".join(transcript_lines)

        summarization_prompt = (
            "You are a product manager assistant. Based on the following conversation, "
            "create a structured task for a software development backlog.\n\n"
            "Return ONLY a JSON object with exactly two keys:\n"
            '- "title": a concise task title (max 80 chars)\n'
            '- "description": a PRD-style description with context, requirements, '
            "and acceptance criteria in markdown\n\n"
            "Conversation transcript:\n"
            f"{transcript}\n\n"
            "Respond with ONLY the JSON object, no other text."
        )

        # Resolve model
        effective_config = dict(self.DEFAULT_MODEL_CONFIG)
        if session.model_config:
            effective_config.update(
                {k: v for k, v in session.model_config.items() if v is not None}
            )
        if model_config:
            effective_config.update({k: v for k, v in model_config.items() if v is not None})

        # Use session's configured model, defaulting to haiku for fast summarization
        model_value = effective_config.get("model", "haiku")

        # Resolve Claude CLI path
        claude_bin = shutil.which("claude") or "claude"

        # Lightweight call: --print (non-interactive, single response)
        cmd = [claude_bin, "--print", "--model", model_value, summarization_prompt]

        # Scrub ANTHROPIC_API_KEY (OAuth-only policy — see core/auth.py).
        from ..utils.subprocess_env import make_subprocess_env

        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("CLAUDECODE", None)

        # Resolve OAuth token (reuse Claude provider logic)
        try:
            provider = get_provider("claude")
            token, _pid, profile_name = provider._resolve_claude_token()
            if token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = token
                logger.info(
                    "[InsightsService] generate_task using profile: %s", sanitize_log(profile_name)
                )
        except Exception:
            pass

        logger.info(
            "[InsightsService] Generating task via claude --print (model=%s)",
            sanitize_log(model_value),
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_path),
                env=env,
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            response = stdout.decode("utf-8", errors="replace").strip()

            stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
            logger.info(
                "[InsightsService] generate_task CLI finished: rc=%s, stdout_len=%s, stderr_len=%s",
                sanitize_log(proc.returncode),
                len(response),
                len(stderr_text),
            )
            if stderr_text:
                logger.info(
                    "[InsightsService] generate_task stderr: %s", sanitize_log(stderr_text[:500])
                )
            if response:
                logger.info(
                    "[InsightsService] generate_task stdout: %s", sanitize_log(response[:300])
                )

            if proc.returncode != 0 and not response:
                logger.error(
                    "[InsightsService] claude CLI exited %s", sanitize_log(proc.returncode)
                )
                return {"title": "", "description": ""}

            if response:
                return _parse_task_json(response)
            return {"title": "", "description": ""}

        except asyncio.TimeoutError:
            logger.error("[InsightsService] generate_task_from_chat timed out (120s)")
            return {"title": "", "description": ""}
        except Exception as e:
            logger.error(
                "[InsightsService] generate_task_from_chat failed: %s",
                sanitize_log(e),
                exc_info=True,
            )
            return {"title": "", "description": ""}

    def clear_session(self, project_path: Path, project_id: str) -> InsightsSession:
        """Clear the current session and create a new one."""
        current_file = self._get_current_session_file(project_path)

        if current_file.exists():
            session_id = current_file.read_text().strip()
            self.delete_session(project_path, session_id)

        return self.create_session(project_path, project_id)


# Global service instance
_insights_service: InsightsService | None = None


def get_insights_service() -> InsightsService:
    """Get the global insights service instance."""
    global _insights_service
    if _insights_service is None:
        _insights_service = InsightsService()
    return _insights_service
