"""
GeminiAgenticProvider — Agentic Gemini CLI adapter for coding/planning phases
==============================================================================

Runs ``gemini --yolo`` as a subprocess, which auto-approves all tool actions
(file reads/writes, command execution) autonomously.  The prompt is sent via
stdin and the CLI's output is streamed back as ``AssistantMessage`` /
``TextBlock`` objects.

Unlike ``GeminiCLIProvider`` (text-only), this provider uses ``--yolo`` mode
which gives Gemini full agentic capabilities without requiring Docker.

Usage::

    from providers.gemini_agentic import GeminiAgenticProvider

    provider = GeminiAgenticProvider(
        model="gemini-3.1-pro-preview",
        working_dir=project_dir,
        timeout=600,
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...

CLI invocation shape::

    gemini --yolo -p <prompt> [--model <model>] [<extra_args>...]
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

from providers import BaseLLMProvider
from providers.gemini import _emit_sunset_warning  # Issue #22
from providers.types import AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

_DEFAULT_GEMINI_PATH: str = "gemini"
_DEFAULT_MODEL: str = "gemini-2.5-pro"
_DEFAULT_TIMEOUT: int = 600  # 10 minutes for agentic tasks
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")


def get_gemini_binary(custom_path: str | None = None) -> str:
    """Dynamically resolve the gemini / antigravity binary path."""
    if custom_path and custom_path != "gemini":
        return custom_path
    if shutil.which("antigravity"):
        return "antigravity"
    from pathlib import Path
    custom_path_default = Path.home() / ".gemini" / "antigravity-cli" / "bin" / "antigravity"
    if custom_path_default.exists():
        return str(custom_path_default)
    if shutil.which("gemini"):
        return "gemini"
    # Fallback to antigravity since we preinstall it by default
    return "antigravity"


class GeminiAgenticProvider(BaseLLMProvider):
    """
    Agentic Gemini provider for coding/planning/spec/qa_fixer phases.

    Runs ``gemini --yolo`` which auto-approves all tool actions (file ops,
    commands) autonomously.  Streams output as AssistantMessage/TextBlock
    messages.

    Args:
        model: Gemini model identifier (e.g. ``"gemini-3.1-pro-preview"``).
        gemini_path: Path or command name for the ``gemini`` executable.
        timeout: Maximum seconds to wait for the subprocess.
        working_dir: Working directory for the subprocess.
        extra_args: Additional CLI flags.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        gemini_path: str = _DEFAULT_GEMINI_PATH,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        _emit_sunset_warning()  # Issue #22 — flag the 2026-06-18 sunset.
        if model and not _MODEL_NAME_RE.match(model):
            raise ValueError(
                f"Invalid model name '{model}': must be alphanumeric with . _ : / - separators"
            )
        self._model = model
        self._gemini_path = gemini_path
        self._timeout = timeout
        self._working_dir = working_dir
        self._extra_args: list[str] = extra_args or []
        for arg in self._extra_args:
            if "\x00" in arg:
                raise ValueError("extra_args must not contain null bytes")
        self._pending_prompt: str | None = None

        logger.debug(
            "GeminiAgenticProvider created model=%s working_dir=%s timeout=%d",
            model,
            working_dir,
            timeout,
        )

    async def query(self, prompt: str) -> None:
        """Store the prompt for execution when ``receive_response()`` is called."""
        self._pending_prompt = prompt

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that runs the Gemini CLI in yolo mode."""
        return self._run_gemini()

    async def _run_gemini(self) -> AsyncGenerator[Any, None]:
        """Spawn gemini --yolo, stream output as AssistantMessage blocks."""
        if not self._pending_prompt:
            logger.warning("GeminiAgenticProvider.receive_response() called before query()")
            return

        resolved_binary = get_gemini_binary(self._gemini_path)
        resolved_path = shutil.which(resolved_binary) if not resolved_binary.startswith("/") else resolved_binary
        if resolved_path is None or (resolved_binary.startswith("/") and not Path(resolved_binary).exists()):
            raise RuntimeError(
                f"Gemini CLI executable not found: '{self._gemini_path}'. "
                "Install the Gemini CLI or pass the correct path."
            )

        cmd = self._build_command()
        cwd = str(self._working_dir) if self._working_dir else None

        logger.debug("GeminiAgenticProvider: spawning cmd=%r cwd=%r", cmd, cwd)

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            prompt_bytes = self._pending_prompt.encode("utf-8")
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=prompt_bytes),
                timeout=float(self._timeout),
            )

        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            raise asyncio.TimeoutError(
                f"Gemini CLI (yolo) timed out after {self._timeout}s."
            )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        logger.debug(
            "GeminiAgenticProvider: finished returncode=%d stdout_len=%d stderr_len=%d",
            proc.returncode,
            len(stdout_text),
            len(stderr_text),
        )

        if proc.returncode != 0 and not stdout_text:
            error_detail = stderr_text or f"exit code {proc.returncode}"
            raise RuntimeError(f"Gemini CLI (yolo) error: {error_detail}")

        if stderr_text:
            logger.warning("Gemini CLI stderr (first 500 chars): %s", stderr_text[:500])

        response_text = stdout_text if stdout_text else "(no output from Gemini CLI)"

        yield AssistantMessage(content=[TextBlock(text=response_text)])

    def _build_command(self) -> list[str]:
        """Build the argv list for ``gemini --yolo -p <prompt>``."""
        resolved_binary = get_gemini_binary(self._gemini_path)
        cmd: list[str] = [resolved_binary, "--yolo"]

        if self._model:
            cmd += ["--model", self._model]

        if self._extra_args:
            cmd.extend(self._extra_args)

        return cmd

    async def __aenter__(self) -> GeminiAgenticProvider:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._pending_prompt = None


__all__ = ["GeminiAgenticProvider"]
