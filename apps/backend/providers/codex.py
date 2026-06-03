"""
CodexCLIProvider — Adapter wrapping the Codex CLI
==================================================

Implements ``BaseLLMProvider`` by spawning the ``codex`` command-line tool
as a subprocess, sending the QA prompt via stdin, and wrapping the plain-text
response in the message-protocol types expected by ``reviewer.py`` and
``fixer.py``.

Since the Codex CLI is a text-in / text-out interface it does not natively
support the agentic tool-use loop (file reads, writes, etc.).  The adapter
therefore sends the **complete prompt** (including all context already
embedded by the QA prompt builder) and returns the LLM's text as a single
``AssistantMessage`` containing one ``TextBlock``.  The QA reviewer logic
in ``reviewer.py`` still reads the ``qa_signoff`` status from
``test_plan.json``, but it will receive the model's analysis as
pure text with no tool calls in the stream.

Usage::

    from pathlib import Path
    from qa.providers.codex import CodexCLIProvider

    provider = CodexCLIProvider(
        model="o4-mini",           # Codex model (passed via --model)
        codex_path="codex",        # executable name / absolute path
        timeout=300,               # subprocess timeout in seconds
        working_dir=project_dir,   # cwd for the subprocess
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...

CLI invocation shape::

    codex -q [--model <model>] [<extra_args>...] --

The ``-q`` flag selects non-interactive (quiet) mode.  The prompt is piped
via stdin to avoid shell quoting issues with multi-kilobyte prompt strings.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

from providers import BaseLLMProvider
from providers.types import AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults (overridable per-instance)
# ---------------------------------------------------------------------------

_DEFAULT_CODEX_PATH: str = "codex"
_DEFAULT_MODEL: str = "o4-mini"
_DEFAULT_TIMEOUT: int = 300  # seconds


class CodexCLIProvider(BaseLLMProvider):
    """
    QA LLM provider backed by the Codex CLI (``codex`` subprocess).

    The adapter invokes the ``codex`` executable as a subprocess, pipes the
    QA prompt via *stdin*, waits for the process to finish, then yields the
    captured stdout as a single ``AssistantMessage`` containing one
    ``TextBlock``.

    Because the Codex CLI does not expose a tool-use protocol, no
    ``ToolUseBlock`` or ``UserMessage`` objects are ever produced.  The QA
    reviewer will receive the model's complete analysis as plain text.

    Args:
        model: Codex model identifier to request (e.g. ``"o4-mini"``,
               ``"o3"``).  Passed to the CLI via ``--model``.  Set to
               ``None`` or ``""`` to omit the flag and let the CLI use its
               own default.
        codex_path: Path or command name for the ``codex`` executable.
                    Resolved via ``$PATH`` when not an absolute path.
                    Defaults to ``"codex"``.
        timeout: Maximum number of seconds to wait for the subprocess to
                 complete before raising ``asyncio.TimeoutError``.
                 Defaults to 300 (5 minutes).
        working_dir: Working directory passed to the subprocess via ``cwd``.
                     Defaults to the process's current directory when
                     ``None``.
        extra_args: Additional CLI flags inserted before the ``--``
                    end-of-flags separator (e.g. ``["--no-git"]``).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        codex_path: str = _DEFAULT_CODEX_PATH,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self._model = model
        self._codex_path = codex_path
        self._timeout = timeout
        self._working_dir = working_dir
        self._extra_args: list[str] = extra_args or []
        self._pending_prompt: str | None = None

        logger.debug(
            "CodexCLIProvider created",
            extra={
                "model": model,
                "codex_path": codex_path,
                "timeout": timeout,
                "working_dir": str(working_dir) if working_dir else None,
            },
        )

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    async def query(self, prompt: str) -> None:
        """Store the prompt for execution when ``receive_response()`` is called.

        Args:
            prompt: The system + user prompt string assembled by the QA
                    prompt builder (may be several kB of text).
        """
        self._pending_prompt = prompt
        logger.debug(
            "CodexCLIProvider: prompt stored (length=%d)", len(prompt)
        )

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that runs the Codex CLI subprocess.

        The generator invokes ``codex`` with the stored prompt as stdin,
        captures stdout, and yields a single ``AssistantMessage``.

        Returns:
            An ``AsyncGenerator`` that yields one ``AssistantMessage``
            containing one ``TextBlock`` with the full CLI response.

        Raises:
            RuntimeError: If the ``codex`` executable cannot be found on
                          ``$PATH``, or if the process exits with a non-zero
                          code *and* produces no stdout.
            asyncio.TimeoutError: If the subprocess exceeds ``self._timeout``
                                  seconds without completing.
        """
        return self._run_codex()

    async def _run_codex(self) -> AsyncGenerator[Any, None]:
        """Async generator: invoke the Codex CLI and yield the response message.

        Yields:
            ``AssistantMessage(content=[TextBlock(text=<response>)])``
        """
        if not self._pending_prompt:
            logger.warning(
                "CodexCLIProvider.receive_response() called before query() — "
                "no prompt to send"
            )
            return

        # Resolve the executable path early so callers get a clear error
        # message rather than a confusing FileNotFoundError from asyncio.
        resolved_path = shutil.which(self._codex_path)
        if resolved_path is None:
            raise RuntimeError(
                f"Codex CLI executable not found: '{self._codex_path}'. "
                "Install the Codex CLI or pass the correct path via "
                "codex_path=... when constructing CodexCLIProvider."
            )

        cmd = self._build_command()
        cwd = str(self._working_dir) if self._working_dir else None

        logger.debug(
            "CodexCLIProvider: spawning subprocess cmd=%r cwd=%r", cmd, cwd
        )

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
                f"Codex CLI subprocess timed out after {self._timeout}s. "
                "Increase timeout= or reduce prompt size."
            )

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        logger.debug(
            "CodexCLIProvider: subprocess finished returncode=%d "
            "stdout_len=%d stderr_len=%d",
            proc.returncode,
            len(stdout_text),
            len(stderr_text),
        )

        # A non-zero exit with no stdout is a fatal error.
        if proc.returncode != 0 and not stdout_text:
            error_detail = stderr_text or f"exit code {proc.returncode}"
            raise RuntimeError(
                f"Codex CLI exited with an error: {error_detail}"
            )

        # Log stderr as a warning when present but non-fatal.
        if stderr_text:
            logger.warning(
                "Codex CLI stderr (first 500 chars): %s",
                stderr_text[:500],
            )

        response_text = stdout_text if stdout_text else "(no output from Codex CLI)"

        yield AssistantMessage(content=[TextBlock(text=response_text)])

    def _build_command(self) -> list[str]:
        """Construct the argv list for the Codex CLI subprocess.

        The resulting command reads the prompt from *stdin* to avoid shell
        quoting issues with long, multi-line prompt strings.

        Shape::

            codex -q [--model <model>] [extra_args...] --

        The ``-q`` flag enables non-interactive (quiet) mode.
        The ``--`` end-of-flags separator signals that subsequent tokens are
        not options; stdin is used for the prompt body.

        Returns:
            A list of strings suitable for ``asyncio.create_subprocess_exec``.
        """
        cmd: list[str] = [self._codex_path, "-q"]

        if self._model:
            cmd += ["--model", self._model]

        if self._extra_args:
            cmd.extend(self._extra_args)

        cmd.append("--")
        return cmd

    # ------------------------------------------------------------------
    # Async context manager — no persistent resources to manage
    # ------------------------------------------------------------------

    async def __aenter__(self) -> CodexCLIProvider:
        """No-op context entry — the subprocess is spawned per-request."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """No-op context exit — clear pending prompt for hygiene."""
        self._pending_prompt = None


__all__ = ["CodexCLIProvider"]
