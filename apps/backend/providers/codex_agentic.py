"""
CodexAgenticProvider — MCP-based Codex adapter for agentic phases
==================================================================

Uses ``codex mcp-server`` (stdio JSON-RPC) instead of ``codex exec``.
The MCP server provides full agentic capability: file creation, command
execution, sandbox control, and multi-turn conversations via threadId.

The server is started once in ``__aenter__`` and reused for all calls
within the ``async with`` block.  Communication follows the MCP protocol
(JSON-RPC 2.0 over stdio, one message per line).

Usage::

    from providers.codex_agentic import CodexAgenticProvider

    provider = CodexAgenticProvider(
        model="gpt-5.3-codex",
        working_dir=spec_dir,
        timeout=600,
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

from providers import BaseLLMProvider
from providers.types import AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

_DEFAULT_CODEX_PATH: str = "codex"
_DEFAULT_MODEL: str = "gpt-5.3-codex"
_DEFAULT_TIMEOUT: int = 600  # 10 minutes for agentic tasks
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")

# MCP protocol constants
_MCP_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "pfactory", "version": "1.0"}


class CodexAgenticProvider(BaseLLMProvider):
    """
    Agentic Codex provider using ``codex mcp-server`` (stdio JSON-RPC).

    Starts a persistent MCP server subprocess on enter, sends tool calls
    to run Codex sessions with full agentic capability, and shuts down
    on exit.

    Args:
        model: Codex model identifier (e.g. ``"gpt-5.3-codex"``).
        codex_path: Path or command name for the ``codex`` executable.
        timeout: Maximum seconds to wait for a response.
        working_dir: Working directory for Codex sessions.
        extra_args: Additional CLI flags (unused in MCP mode, kept for API compat).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        codex_path: str = _DEFAULT_CODEX_PATH,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        if model and not _MODEL_NAME_RE.match(model):
            raise ValueError(
                f"Invalid model name '{model}': must be alphanumeric with . _ : / - separators"
            )
        self._model = model
        self._codex_path = codex_path
        self._timeout = timeout
        self._working_dir = working_dir
        self._extra_args: list[str] = extra_args or []
        self._pending_prompt: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._request_id: int = 0
        self._thread_id: str | None = None

        logger.debug(
            "CodexAgenticProvider created model=%s working_dir=%s timeout=%d",
            model,
            working_dir,
            timeout,
        )

    async def _send_message(self, message: dict) -> None:
        """Send a JSON-RPC message to the MCP server via stdin."""
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("MCP server not running")
        line = json.dumps(message) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _read_response(self, expected_id: int) -> dict:
        """Read a JSON-RPC response from the MCP server stdout.

        Skips notification messages (no 'id' field) and waits for
        the response matching the expected request ID.
        """
        if not self._proc or not self._proc.stdout:
            raise RuntimeError("MCP server not running")

        while True:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(),
                timeout=float(self._timeout),
            )
            if not line:
                raise RuntimeError("MCP server closed stdout unexpectedly")

            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("CodexMCP: skipping non-JSON line: %s", text[:200])
                continue

            # Skip notifications (no id field)
            if "id" not in data:
                continue

            if data.get("id") == expected_id:
                if "error" in data:
                    error = data["error"]
                    raise RuntimeError(
                        f"MCP error {error.get('code', '?')}: {error.get('message', 'unknown')}"
                    )
                return data

    def _next_id(self) -> int:
        """Get the next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    @staticmethod
    def _build_subprocess_env() -> dict[str, str]:
        """Build the env for ``codex mcp-server``, owning auth when possible.

        Codex resolves credentials from ``$CODEX_HOME/auth.json`` (default
        ``~/.codex``). A bare ChatGPT-account login there rejects every model
        ("model is not supported when using Codex with a ChatGPT account"),
        which is fragile: any re-login to ChatGPT silently breaks PFactory.

        When ``OPENAI_API_KEY`` is set, point Codex at a PFactory-owned
        ``CODEX_HOME`` containing an api-key ``auth.json``
        (``{"OPENAI_API_KEY": ...}`` — the exact shape ``codex login
        --with-api-key`` writes). This makes agentic Codex work regardless of
        the user's global ``codex login`` state, and leaves that global login
        untouched. With no API key, fall back to the inherited environment so a
        Codex-enabled ChatGPT plan still works.
        """
        env = dict(os.environ)
        api_key = env.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return env

        codex_home = Path.home() / ".pfactory" / "codex-home"
        try:
            codex_home.mkdir(parents=True, exist_ok=True)
            auth_path = codex_home / "auth.json"
            desired = json.dumps({"OPENAI_API_KEY": api_key})
            # Only rewrite when the key changed — avoids needless disk churn.
            if not auth_path.exists() or auth_path.read_text().strip() != desired:
                auth_path.write_text(desired)
                auth_path.chmod(0o600)
            env["CODEX_HOME"] = str(codex_home)
            logger.info(
                "CodexAgenticProvider: using PFactory-owned CODEX_HOME=%s (api-key auth)",
                codex_home,
            )
        except OSError as exc:
            # Non-fatal: fall back to the inherited codex login.
            logger.warning(
                "CodexAgenticProvider: could not provision CODEX_HOME (%s); "
                "falling back to global codex login",
                exc,
            )
        return env

    async def __aenter__(self) -> CodexAgenticProvider:
        """Start the MCP server and send initialize handshake."""
        # "codex" is often a shell alias (e.g. -> codex-cli), which shutil.which
        # cannot resolve for create_subprocess_exec. Fall back to the real
        # binary name so agentic Codex works in those environments.
        resolved_path = shutil.which(self._codex_path) or shutil.which("codex-cli")
        if resolved_path is None:
            raise RuntimeError(
                f"Codex CLI executable not found: '{self._codex_path}' "
                "(also tried 'codex-cli'). Install the Codex CLI or pass the correct path."
            )

        cmd = [resolved_path, "mcp-server"]
        logger.info("CodexAgenticProvider: starting MCP server: %s", " ".join(cmd))

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_subprocess_env(),
        )

        # Send initialize
        init_id = self._next_id()
        await self._send_message({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        })

        response = await self._read_response(init_id)
        server_info = response.get("result", {}).get("serverInfo", {})
        logger.info(
            "CodexAgenticProvider: MCP server initialized — %s v%s",
            server_info.get("name", "unknown"),
            server_info.get("version", "?"),
        )

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Shut down the MCP server subprocess."""
        self._pending_prompt = None
        self._thread_id = None

        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
            finally:
                self._proc = None
                logger.debug("CodexAgenticProvider: MCP server stopped")

    async def query(self, prompt: str) -> None:
        """Store the prompt for execution when ``receive_response()`` is called."""
        self._pending_prompt = prompt

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that calls the Codex MCP tool."""
        return self._run_codex_mcp()

    async def _run_codex_mcp(self) -> AsyncGenerator[Any, None]:
        """Call the 'codex' tool via MCP and yield the response."""
        if not self._pending_prompt:
            logger.warning("CodexAgenticProvider.receive_response() called before query()")
            return

        if not self._proc:
            raise RuntimeError("MCP server not running — use 'async with' context manager")

        # Build tool call arguments
        arguments: dict[str, Any] = {
            "prompt": self._pending_prompt,
            "approval-policy": "never",
            "sandbox": "danger-full-access",
        }

        if self._model:
            arguments["model"] = self._model

        if self._working_dir:
            arguments["cwd"] = str(self._working_dir)

        # Use codex-reply for multi-turn if we have a thread ID
        tool_name = "codex"
        if self._thread_id:
            tool_name = "codex-reply"
            arguments["threadId"] = self._thread_id

        call_id = self._next_id()
        await self._send_message({
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        })

        logger.info(
            "CodexAgenticProvider: sent %s call (id=%d, model=%s, cwd=%s)",
            tool_name,
            call_id,
            self._model,
            self._working_dir,
        )

        response = await self._read_response(call_id)

        # Extract response text
        result = response.get("result", {})

        # The codex MCP server reports model/stream failures as an ``error``
        # field on an *otherwise successful* tools/call result (not a JSON-RPC
        # error), e.g. {"error": "... 400 ... model is not supported when using
        # Codex with a ChatGPT account."}. Surface it loudly — otherwise the
        # caller silently receives "(no output from Codex MCP)" and the agent
        # fails with an opaque "missing output" instead of the real cause.
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"Codex MCP run failed: {result['error']}")

        content_blocks = result.get("content", [])
        structured = result.get("structuredContent", {})

        # Store thread ID for potential multi-turn
        thread_id = structured.get("threadId")
        if thread_id:
            self._thread_id = thread_id

        # Extract text from content blocks
        response_text = ""
        for block in content_blocks:
            if block.get("type") == "text":
                response_text += block.get("text", "")

        if not response_text:
            response_text = structured.get("content", "(no output from Codex MCP)")

        logger.info(
            "CodexAgenticProvider: response received (len=%d, threadId=%s)",
            len(response_text),
            thread_id or "none",
        )

        yield AssistantMessage(content=[TextBlock(text=response_text)])


__all__ = ["CodexAgenticProvider"]
