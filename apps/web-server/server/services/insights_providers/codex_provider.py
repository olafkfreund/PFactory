"""
Codex CLI (OpenAI) provider for insights chat.

Runs `codex exec --model <model> "<message>"` as a subprocess.
"""

import asyncio
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

from ...websockets.events import broadcast_event
from .base import ProviderInfo, ProviderModel, ProviderStrategy

logger = logging.getLogger(__name__)

# Codex models (static fallback list)
CODEX_MODELS = [
    ProviderModel(id="gpt-5.5", label="GPT-5.5"),
    ProviderModel(id="gpt-5.4", label="GPT-5.4"),
    ProviderModel(id="gpt-5.4-mini", label="GPT-5.4 Mini"),
    ProviderModel(id="gpt-5.4-nano", label="GPT-5.4 Nano"),
    ProviderModel(id="gpt-5.3-codex", label="GPT-5.3 Codex"),
]


class CodexProvider(ProviderStrategy):
    """Provider that shells out to the Codex CLI."""

    async def detect(self) -> ProviderInfo:
        # Reuse cli_accounts detection logic
        from ...routes.cli_accounts import _detect_cli_version, _detect_codex_credentials

        version = _detect_cli_version("codex")
        installed = version is not None
        authenticated, auth_method, _ = (False, None, None)

        if installed:
            authenticated, auth_method, _ = _detect_codex_credentials()

        return ProviderInfo(
            provider="codex",
            available=installed and authenticated,
            display_name="Codex (OpenAI)",
            icon="openai",
            auth_method=auth_method,
            models=CODEX_MODELS if installed and authenticated else [],
        )

    async def send_message(
        self,
        project_path: Path,
        project_id: str,
        message: str,
        model: str | None,
        model_config: dict | None,
        conversation_history: list[dict] | None,
    ) -> str:
        cmd = ["bash", "-l", "-c"]

        effective_model = model or (model_config or {}).get("model", "gpt-5.3-codex")

        # Build prompt with conversation context for stateless CLI
        full_prompt = message
        if conversation_history:
            context_parts = []
            for msg in conversation_history[-6:]:  # Last 6 messages for context
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]
                context_parts.append(f"[{role}]: {content}")
            if context_parts:
                full_prompt = "\n".join(context_parts) + f"\n[user]: {message}"

        codex_cmd = f"codex exec --model {shlex.quote(effective_model)} {shlex.quote(full_prompt)}"

        cmd.append(codex_cmd)

        # Scrub ANTHROPIC_API_KEY (OAuth-only policy — see core/auth.py).
        from ...utils.subprocess_env import make_subprocess_env
        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"

        logger.info(f"[CodexProvider] Starting: codex exec --model {effective_model}")

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

            accumulated = ""
            stream_start = time.monotonic()
            async for line_bytes in proc.stdout:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                accumulated += line + "\n"
                await broadcast_event("insights:chunk", {
                    "projectId": project_id,
                    "type": "text",
                    "content": line + "\n",
                })

            await proc.wait()

            stderr_output = await proc.stderr.read()
            if proc.returncode != 0 and not accumulated.strip():
                stderr_text = stderr_output.decode("utf-8", errors="replace").strip() if stderr_output else ""
                error_msg = stderr_text or f"Codex CLI exited with code {proc.returncode}"
                await broadcast_event("insights:chunk", {
                    "projectId": project_id,
                    "type": "error",
                    "error": error_msg,
                })
                return ""

            elapsed = time.monotonic() - stream_start
            estimated_tokens = max(1, len(accumulated) // 4)
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

            return accumulated

        except Exception as e:
            logger.error(f"[CodexProvider] Error: {e}", exc_info=True)
            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "error",
                "error": str(e),
            })
            return ""
