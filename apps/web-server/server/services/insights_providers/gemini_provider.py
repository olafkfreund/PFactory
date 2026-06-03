"""
Gemini CLI (Google) provider for insights chat.

Runs `gemini --prompt "<message>"` as a subprocess.
"""

import asyncio
import logging
import os
import shlex
import shutil
import time
from pathlib import Path

from ...websockets.events import broadcast_event
from .base import ProviderInfo, ProviderModel, ProviderStrategy

logger = logging.getLogger(__name__)

# Gemini models (static fallback list)
GEMINI_MODELS = [
    ProviderModel(id="gemini-3.1-pro-preview", label="Gemini 3.1 Pro (Preview)"),
    ProviderModel(id="gemini-2.5-pro", label="Gemini 2.5 Pro"),
    ProviderModel(id="gemini-3.5-flash", label="Gemini 3.5 Flash"),
    ProviderModel(id="gemini-3.1-flash-lite", label="Gemini 3.1 Flash-Lite"),
    ProviderModel(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
]


class GeminiProvider(ProviderStrategy):
    """Provider that shells out to the Gemini CLI."""

    async def detect(self) -> ProviderInfo:
        from ...routes.cli_accounts import _detect_gemini_credentials, get_gemini_binary

        # Fast path: just check if gemini/antigravity binary exists on PATH
        # (running `gemini --version` takes ~3s due to Node.js startup)
        binary = get_gemini_binary()
        installed = (shutil.which(binary) is not None) if not binary.startswith("/") else Path(binary).exists()

        authenticated, auth_method, _ = (False, None, None)
        if installed:
            authenticated, auth_method, _ = _detect_gemini_credentials()

        return ProviderInfo(
            provider="gemini",
            available=installed and authenticated,
            display_name="Gemini (Google)",
            icon="gemini",
            auth_method=auth_method,
            models=GEMINI_MODELS if installed and authenticated else [],
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
        from ...routes.cli_accounts import get_gemini_binary
        cmd = ["bash", "-l", "-c"]

        effective_model = model or (model_config or {}).get("model", "gemini-2.5-flash")

        # Build prompt with conversation context for stateless CLI
        full_prompt = message
        if conversation_history:
            context_parts = []
            for msg in conversation_history[-6:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]
                context_parts.append(f"[{role}]: {content}")
            if context_parts:
                full_prompt = "\n".join(context_parts) + f"\n[user]: {message}"

        binary = get_gemini_binary()
        gemini_cmd = f"{shlex.quote(binary)} --model {shlex.quote(effective_model)} --prompt {shlex.quote(full_prompt)}"

        cmd.append(gemini_cmd)

        # Scrub ANTHROPIC_API_KEY (OAuth-only policy — see core/auth.py).
        from ...utils.subprocess_env import make_subprocess_env
        env = make_subprocess_env()
        env["PYTHONUNBUFFERED"] = "1"

        logger.info(f"[GeminiProvider] Starting: gemini --model {effective_model}")

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
                error_msg = stderr_text or f"Gemini CLI exited with code {proc.returncode}"
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
            logger.error(f"[GeminiProvider] Error: {e}", exc_info=True)
            await broadcast_event("insights:chunk", {
                "projectId": project_id,
                "type": "error",
                "error": str(e),
            })
            return ""
