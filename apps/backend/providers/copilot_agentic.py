"""
CopilotAgenticProvider — GitHub Copilot CLI adapter for agentic phases
======================================================================

Drives the GitHub Copilot CLI (``copilot``) in headless, single-shot mode::

    copilot -p "<prompt>" --allow-all-tools --model <model>

Copilot runs its *own* agentic tool loop inside that one invocation (it reads
files, writes files, runs commands via ``--allow-all-tools``), so unlike the
Codex MCP provider there is no JSON-RPC turn loop to manage here — the provider
launches the process once, lets Copilot do the work in ``working_dir``, and
returns the final assistant narration.

Auth + billing come from the user's GitHub Copilot subscription (``copilot``
must already be signed in). Models are whatever the installed CLI exposes —
currently ``claude-sonnet-4.5`` (default), ``claude-sonnet-4`` and ``gpt-5``.

Usage::

    from providers.copilot_agentic import CopilotAgenticProvider

    provider = CopilotAgenticProvider(model="claude-sonnet-4.5", working_dir=spec_dir)
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...
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
from providers.types import AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

_DEFAULT_COPILOT_PATH: str = "copilot"
_DEFAULT_MODEL: str = "claude-sonnet-4.5"
_DEFAULT_TIMEOUT: int = 600  # 10 minutes for agentic tasks
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")

# Lines from here onward are Copilot's post-run usage/billing summary, not
# model output — trimmed before returning the assistant text.
_TRAILER_RE = re.compile(r"^(Total (usage|duration|code changes)|Usage by model:)", re.M)


class CopilotAgenticProvider(BaseLLMProvider):
    """
    Agentic provider backed by the GitHub Copilot CLI (single-shot ``-p``).

    Copilot performs the full tool loop internally, so this provider is a thin
    subprocess wrapper: it resolves the binary on enter, runs one ``copilot -p``
    per query in ``working_dir``, strips the usage trailer, and yields the
    assistant text as a single ``AssistantMessage``.

    Args:
        model: Copilot model identifier (e.g. ``"claude-sonnet-4.5"``). The CLI
            validates this against its own allowed set.
        copilot_path: Path or command name for the ``copilot`` executable.
        timeout: Maximum seconds to wait for the run to finish.
        working_dir: Directory Copilot operates in (its file tools write here).
        extra_args: Extra CLI flags appended to the invocation.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        copilot_path: str = _DEFAULT_COPILOT_PATH,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        # Callers (the phase resolver) thread the provider-prefixed form
        # ``copilot:claude-sonnet-4.5`` because that's how PFactory routes the
        # provider. The CLI only accepts the bare model name, so strip it once
        # here (mirrors OllamaAgenticProvider).
        if model and model.lower().startswith("copilot:"):
            model = model[len("copilot:"):]
        if model and not _MODEL_NAME_RE.match(model):
            raise ValueError(
                f"Invalid model name '{model}': must be alphanumeric with . _ : / - separators"
            )
        self._model = model or _DEFAULT_MODEL
        self._copilot_path = copilot_path
        self._timeout = timeout
        self._working_dir = working_dir
        self._extra_args: list[str] = extra_args or []
        self._pending_prompt: str | None = None
        self._resolved_path: str | None = None

        logger.debug(
            "CopilotAgenticProvider created model=%s working_dir=%s timeout=%d",
            self._model,
            working_dir,
            timeout,
        )

    async def __aenter__(self) -> CopilotAgenticProvider:
        """Resolve the copilot binary (no persistent process — runs are one-shot)."""
        resolved = shutil.which(self._copilot_path) or shutil.which("github-copilot")
        if resolved is None:
            raise RuntimeError(
                f"Copilot CLI executable not found: '{self._copilot_path}'. "
                "Install the GitHub Copilot CLI and sign in (`copilot`)."
            )
        self._resolved_path = resolved
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._pending_prompt = None
        self._resolved_path = None

    async def query(self, prompt: str) -> None:
        """Store the prompt for execution when ``receive_response()`` is called."""
        self._pending_prompt = prompt

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that runs the Copilot CLI and yields its output."""
        return self._run_copilot()

    @staticmethod
    def _strip_trailer(text: str) -> str:
        """Drop Copilot's post-run usage/billing summary from captured stdout."""
        match = _TRAILER_RE.search(text)
        if match:
            text = text[: match.start()]
        return text.strip()

    async def _run_copilot(self) -> AsyncGenerator[Any, None]:
        if not self._pending_prompt:
            logger.warning("CopilotAgenticProvider.receive_response() called before query()")
            return
        if not self._resolved_path:
            raise RuntimeError("Copilot CLI not resolved — use 'async with' context manager")

        cmd = [
            self._resolved_path,
            "-p",
            self._pending_prompt,
            "--allow-all-tools",
            "--model",
            self._model,
            *self._extra_args,
        ]
        logger.info(
            "CopilotAgenticProvider: running copilot (model=%s, cwd=%s)",
            self._model,
            self._working_dir,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._working_dir) if self._working_dir else None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=float(self._timeout)
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(
                f"Copilot run exceeded timeout ({self._timeout}s)"
            ) from None

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            detail = (stderr or stdout).strip()[:500]
            raise RuntimeError(
                f"Copilot CLI exited {proc.returncode}: {detail or '(no output)'}"
            )

        response_text = self._strip_trailer(stdout) or "(no output from Copilot CLI)"
        logger.info(
            "CopilotAgenticProvider: run complete (len=%d, rc=%d)",
            len(response_text),
            proc.returncode,
        )
        yield AssistantMessage(content=[TextBlock(text=response_text)])


__all__ = ["CopilotAgenticProvider"]
