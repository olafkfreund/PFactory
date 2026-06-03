"""
Claude CLI provider for insights chat.

Extracted from InsightsService — runs `claude --print --output-format stream-json`.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ...websockets.events import broadcast_event
from .base import ProviderInfo, ProviderModel, ProviderStrategy

logger = logging.getLogger(__name__)

# Claude models (static — CLI supports these shorthands)
CLAUDE_MODELS = [
    ProviderModel(id="opus", label="Claude Opus 4.7"),
    ProviderModel(id="sonnet", label="Claude Sonnet 4.6"),
    ProviderModel(id="haiku", label="Claude Haiku 4.5"),
]


class ClaudeProvider(ProviderStrategy):
    """Provider that shells out to the Claude Code CLI."""

    def __init__(self) -> None:
        self._claude_path: str | None = None

    # ------------------------------------------------------------------
    # Detection (reuses InsightsService._resolve_claude_path logic)
    # ------------------------------------------------------------------

    def _resolve_claude_path(self) -> str:
        if self._claude_path:
            return self._claude_path

        path = shutil.which("claude")
        if path:
            self._claude_path = path
            return path

        try:
            result = subprocess.run(
                ["bash", "-l", "-c", "which claude"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                self._claude_path = result.stdout.strip()
                return self._claude_path
        except (subprocess.SubprocessError, OSError):
            pass

        home = Path.home()
        for candidate in [
            home / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
        ]:
            if candidate.exists():
                self._claude_path = str(candidate)
                return self._claude_path

        return "claude"

    def _resolve_claude_token(self) -> tuple[str | None, str | None, str | None]:
        from ...config import get_settings
        settings = get_settings()

        env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if env_token:
            return (env_token, "env-override", "Environment Override")

        profiles_file = Path(settings.PROJECTS_DATA_DIR) / "claude-profiles.json"
        from ...paths import get_data_file
        legacy_profiles_file = get_data_file("claude-profiles.json")
        if not profiles_file.exists() and legacy_profiles_file.exists():
            profiles_file = legacy_profiles_file

        if profiles_file.exists():
            try:
                data = json.loads(profiles_file.read_text())
                profiles = data.get("profiles", [])
                active_id = data.get("activeProfileId")
                usable = [p for p in profiles if p.get("oauthToken") or p.get("token")]

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

        token_file = Path.home() / ".claude" / "oauth_token"
        if token_file.exists():
            token = token_file.read_text().strip()
            if token:
                return (token, "static-fallback", "Static Token")

        return (None, None, None)

    async def detect(self) -> ProviderInfo:
        claude_bin = self._resolve_claude_path()
        available = shutil.which(claude_bin) is not None or claude_bin != "claude"

        token, _, profile_name = self._resolve_claude_token()
        auth = None
        if token:
            auth = f"OAuth ({profile_name})" if profile_name else "OAuth"

        return ProviderInfo(
            provider="claude",
            available=available and token is not None,
            display_name="Claude",
            icon="sparkles",
            auth_method=auth,
            models=CLAUDE_MODELS,
        )

    # ------------------------------------------------------------------
    # Message sending (extracted from InsightsService.send_message)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model: str | None,
        model_config: dict | None,
        conversation_history: list[dict] | None,
    ) -> str:
        claude_bin = self._resolve_claude_path()
        cmd = [
            claude_bin,
            "--print",
            "--verbose",
            "--output-format", "stream-json",
        ]

        if model_config:
            model_value = model_config.get("model") or model
            if model_value:
                cmd.extend(["--model", model_value])

            thinking_level = model_config.get("thinkingLevel")
            if thinking_level and thinking_level != "none":
                effort_map = {"low": "low", "medium": "medium", "high": "high"}
                effort = effort_map.get(thinking_level)
                if effort:
                    cmd.extend(["--effort", effort])
        elif model:
            cmd.extend(["--model", model])

        cmd.append(message)

        # Scrub ANTHROPIC_API_KEY (OAuth-only policy — see core/auth.py).
        # The Claude CLI we spawn here would happily use the direct-API
        # key if it inherited one; we want OAuth via CLAUDE_CODE_OAUTH_TOKEN.
        from ...utils.subprocess_env import make_subprocess_env
        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("CLAUDECODE", None)

        token, profile_id, profile_name = self._resolve_claude_token()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
            logger.info(f"[ClaudeProvider] Using profile: {profile_name} ({profile_id})")
        else:
            logger.warning("[ClaudeProvider] No OAuth token available")

        logger.info(f"[ClaudeProvider] Starting CLI: {' '.join(cmd[:5])}...")

        try:
            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "text",
                "content": "",
            })

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(project_path),
                env=env,
            )

            accumulated_content = ""
            tools_used = []
            stream_start = time.monotonic()

            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue

                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                        event_type = data.get("type", "")

                        if event_type == "assistant":
                            content = data.get("message", {}).get("content", "")
                            if isinstance(content, list):
                                for block in content:
                                    if block.get("type") == "text":
                                        text = block.get("text", "")
                                        accumulated_content += text
                                        await broadcast_event("insights:chunk", {
                                            "projectId": project_id,
                                            "type": "text",
                                            "content": text,
                                        })
                            elif isinstance(content, str):
                                accumulated_content += content
                                await broadcast_event("insights:chunk", {
                                    "projectId": project_id,
                                    "type": "text",
                                    "content": content,
                                })

                        elif event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                accumulated_content += text
                                await broadcast_event("insights:chunk", {
                                    "projectId": project_id,
                                    "type": "text",
                                    "content": text,
                                })

                        elif event_type == "tool_use":
                            tool_name = data.get("name", data.get("tool", "Unknown"))
                            tool_input = data.get("input", "")
                            if isinstance(tool_input, dict):
                                tool_input = tool_input.get("file_path") or tool_input.get("pattern") or str(tool_input)[:100]
                            tools_used.append({
                                "name": tool_name,
                                "input": str(tool_input)[:200],
                                "timestamp": datetime.now().isoformat(),
                            })
                            await broadcast_event("insights:chunk", {
                                "projectId": project_id,
                                "type": "tool_start",
                                "tool": {"name": tool_name, "input": str(tool_input)[:200]},
                            })

                        elif event_type == "tool_result":
                            await broadcast_event("insights:chunk", {
                                "projectId": project_id,
                                "type": "tool_end",
                            })

                        elif event_type == "result":
                            result = data.get("result", "")
                            if result and result != accumulated_content:
                                accumulated_content = result
                                await broadcast_event("insights:chunk", {
                                    "projectId": project_id,
                                    "type": "text",
                                    "content": result,
                                })

                        continue
                    except json.JSONDecodeError:
                        pass

                accumulated_content += line + "\n"
                await broadcast_event("insights:chunk", {
                    "projectId": project_id,
                    "type": "text",
                    "content": line + "\n",
                })

            await proc.wait()

            stderr_output = await proc.stderr.read()
            stderr_text = ""
            if stderr_output:
                stderr_text = stderr_output.decode("utf-8", errors="replace").strip()
                logger.warning(f"[ClaudeProvider] stderr: {stderr_text}")

            if proc.returncode != 0 and not accumulated_content.strip():
                error_msg = stderr_text or f"Claude CLI exited with code {proc.returncode}"
                logger.error(f"[ClaudeProvider] CLI failed: {error_msg}")
                await broadcast_event("insights:chunk", {
                    "projectId": project_id,
                    "type": "error",
                    "error": error_msg,
                })
                return ""

            elapsed = time.monotonic() - stream_start
            # Estimate tokens: ~4 chars per token for English text
            estimated_tokens = max(1, len(accumulated_content) // 4)
            tokens_per_sec = round(estimated_tokens / elapsed, 1) if elapsed > 0 else 0

            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "done",
                "metrics": {
                    "outputTokens": estimated_tokens,
                    "tokensPerSecond": tokens_per_sec,
                    "elapsedSeconds": round(elapsed, 1),
                    "estimated": True,
                },
            })

            return accumulated_content

        except Exception as e:
            logger.error(f"[ClaudeProvider] Error: {e}", exc_info=True)
            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "error",
                "error": str(e),
            })
            return ""
