"""
OllamaAgenticProvider — Agentic Ollama with native tool calling
================================================================

Multi-turn conversation loop using Ollama's native tool-calling protocol.
Models like qwen3, llama3.x, and mistral can receive tool definitions,
return ``tool_calls`` in their response, and continue the conversation
with tool results — all via the standard ``/api/chat`` endpoint.

Unlike Codex/Gemini agentic providers (which delegate to their CLI), this
provider implements its own tool execution loop using the reusable
``tools.ToolExecutor``.

Architecture::

    Ollama API loop:                    Tool Executor (reusable):
    +-----------------+                 +------------------+
    | Send prompt +   | --tool_calls--> | Execute locally: |
    | tool definitions|                 |  Read, Write,    |
    | to /api/chat    | <--results----  |  Edit, Bash,     |
    |                 |                 |  Glob, Grep      |
    | Repeat until    |                 |                  |
    | no tool_calls   |                 | Security:        |
    +-----------------+                 |  - path boundary |
                                        |  - bash allowlist|
                                        +------------------+

Protocol: yields the same message types (``AssistantMessage``, ``UserMessage``,
``TextBlock``, ``ToolUseBlock``, ``ToolResultBlock``) as the Claude SDK, so
``reviewer.py`` and ``fixer.py`` work unmodified.

Usage::

    from pathlib import Path
    from providers.ollama_agentic import OllamaAgenticProvider

    provider = OllamaAgenticProvider(
        model="qwen3:30b",
        working_dir=Path("/path/to/project"),
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...  # AssistantMessage / UserMessage with tool results
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

from providers import BaseLLMProvider
from providers.types import (
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from tools.definitions import get_tool_definitions
from tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL: str = "http://localhost:11434"
_DEFAULT_MODEL: str = "llama3.2"
_DEFAULT_TIMEOUT: int = 600  # seconds per request (agentic needs more time)
_DEFAULT_MAX_TURNS: int = 25
_DEFAULT_TOOL_NAMES: list[str] = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

_PATH_CHAT: str = "/api/chat"
_PATH_TAGS: str = "/api/tags"
_MAX_TOOL_ARGS_LEN: int = 50_000  # 50 KB safety limit for tool argument strings


def _extract_text_tool_calls(text: str) -> list[dict]:
    """Parse tool calls a model emitted as TEXT (a ```json {"name","arguments"}```
    block, or a bare top-level JSON object with those keys) into the native
    ``tool_calls`` shape ``[{"function": {"name", "arguments"}}]``.

    Small/local models (qwen, llama, etc.) often describe a tool call in prose
    instead of using the structured tool_calls field; this lets the agentic
    loop execute them anyway.
    """
    import re

    out: list[dict] = []
    candidates: list[str] = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if not candidates:
        stripped = text.strip()
        if stripped.startswith("{") and '"name"' in stripped and '"arguments"' in stripped:
            candidates = [stripped]
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("name") and "arguments" in obj:
            out.append({"function": {"name": obj["name"], "arguments": obj["arguments"]}})
    return out


class OllamaAgenticProvider(BaseLLMProvider):
    """Agentic Ollama provider with native tool calling.

    Sends prompts to Ollama's ``/api/chat`` with tool definitions.  When the
    model returns ``tool_calls``, executes them locally via ``ToolExecutor``
    and feeds results back for multi-turn conversation.

    Args:
        model: Ollama model tag (e.g. ``"qwen3:30b"``).  Must already be
            pulled on the local Ollama server and support tool calling.
        base_url: Ollama server base URL.
        timeout: Per-request HTTP timeout in seconds.
        working_dir: Project directory — security boundary for all file
            operations and bash execution root.
        max_turns: Maximum conversation turns before stopping (prevents
            infinite loops).
        tool_names: List of tool names to enable.  Defaults to all six.
        extra_options: Ollama model parameters (temperature, etc.).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: Path | str = Path("."),
        max_turns: int = _DEFAULT_MAX_TURNS,
        tool_names: list[str] | None = None,
        extra_options: dict[str, Any] | None = None,
        extra_roots: list[Path] | None = None,
    ) -> None:
        # Callers (e.g. the phase resolver) pass the provider-prefixed form
        # ``ollama:qwen3:14b`` because that's how PFactory threads the
        # provider hint through the system. Ollama itself only knows the
        # bare tag (``qwen3:14b``) and returns HTTP 400 if you send the
        # prefix in /api/chat's ``model`` field. Strip it once here.
        if model.startswith("ollama:"):
            model = model[len("ollama:") :]
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._working_dir = Path(working_dir).resolve()
        self._max_turns = max_turns
        self._extra_options: dict[str, Any] = extra_options or {}
        # Default to 32K context for complex tasks
        if "num_ctx" not in self._extra_options:
            self._extra_options["num_ctx"] = 32768
        self._pending_prompt: str | None = None

        # Tool setup
        effective_tools = tool_names or _DEFAULT_TOOL_NAMES
        self._tool_defs = get_tool_definitions(effective_tools)
        # The SUT project dir is the primary boundary, but PFactory's per-task
        # spec/workspace dir (where test_plan.json + generated tests are
        # written) lives outside it — thread it in as an extra allowed root so
        # the agent's writes aren't denied. See ToolExecutor.extra_roots.
        self._extra_roots: list[Path] = [Path(r).resolve() for r in (extra_roots or [])]
        self._executor = ToolExecutor(
            working_dir=self._working_dir,
            bash_timeout=min(timeout, 120),
            extra_roots=self._extra_roots,
        )

        logger.debug(
            "OllamaAgenticProvider created: model=%s tools=%s max_turns=%d",
            model,
            effective_tools,
            max_turns,
        )

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    async def query(self, prompt: str) -> None:
        """Store the prompt for execution when ``receive_response()`` is called."""
        self._pending_prompt = prompt
        logger.debug("OllamaAgenticProvider: prompt stored (length=%d)", len(prompt))

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that runs the agentic tool-calling loop."""
        return self._run_agentic_loop()

    async def _run_agentic_loop(self) -> AsyncGenerator[Any, None]:
        """Multi-turn agentic loop with tool execution.

        1. Send messages + tool definitions to ``/api/chat``
        2. If response has ``tool_calls``: execute tools, append results, loop
        3. If no ``tool_calls``: yield final text response and stop
        4. Safety: stop after ``max_turns`` iterations

        Yields:
            ``AssistantMessage`` and ``UserMessage`` objects matching the
            Claude SDK message protocol.
        """
        if not self._pending_prompt:
            logger.warning("OllamaAgenticProvider.receive_response() called before query()")
            return

        # Build initial messages
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._pending_prompt},
        ]

        # Convergence guard for small/local models: they tend to loop on Read
        # and never emit the final Write. Track signatures of executed calls and
        # how many read-only turns have passed without producing output, then
        # nudge the model to write once a threshold is crossed.
        _seen_calls: set[tuple[str, str]] = set()
        _readonly_turns = 0
        _produced_output = False
        _nudged = False
        _READONLY = {"Read", "Glob", "Grep"}
        _WRITERS = {"Write", "Edit", "MultiEdit"}
        _NUDGE_AFTER = 5

        for turn in range(self._max_turns):
            logger.debug("OllamaAgenticProvider: turn %d/%d", turn + 1, self._max_turns)

            # Call Ollama API
            payload = self._build_payload(messages)
            url = f"{self._base_url}{_PATH_CHAT}"

            try:
                response_data = await asyncio.wait_for(
                    asyncio.to_thread(self._http_post, url, payload),
                    timeout=float(self._timeout),
                )
            except TimeoutError:
                yield AssistantMessage(
                    content=[
                        TextBlock(
                            text=f"[Ollama request timed out after {self._timeout}s on turn {turn + 1}]"
                        )
                    ]
                )
                return

            # Parse response
            message = response_data.get("message", {})
            content_text = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls")

            # Fallback for small/local models (e.g. qwen2.5-coder) that emit a
            # tool call as a ```json {"name","arguments"}``` TEXT block instead
            # of the native tool_calls field — parse them so the agentic loop
            # still progresses (otherwise the agent stalls and writes nothing).
            if not tool_calls and content_text:
                parsed = _extract_text_tool_calls(content_text)
                if parsed:
                    tool_calls = parsed
                    content_text = ""  # don't surface the raw JSON block as text

            # Build the AssistantMessage content blocks
            assistant_blocks: list[Any] = []
            if content_text:
                assistant_blocks.append(TextBlock(text=content_text))

            if tool_calls:
                # Model wants to call tools
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    tool_args = fn.get("arguments", {})
                    # Ensure arguments is a dict (some models send a string)
                    if isinstance(tool_args, str):
                        if len(tool_args) > _MAX_TOOL_ARGS_LEN:
                            logger.warning(
                                "Tool arguments exceed size limit (%d > %d), discarding",
                                len(tool_args),
                                _MAX_TOOL_ARGS_LEN,
                            )
                            tool_args = {}
                        else:
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {}

                    assistant_blocks.append(ToolUseBlock(name=tool_name, input=tool_args))

                # Yield the assistant message with text + tool use blocks
                yield AssistantMessage(content=assistant_blocks)

                # Execute tools and collect results
                tool_result_blocks: list[Any] = []
                tool_results_for_api: list[dict[str, Any]] = []

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    tool_args = fn.get("arguments", {})
                    if isinstance(tool_args, str):
                        if len(tool_args) > _MAX_TOOL_ARGS_LEN:
                            logger.warning(
                                "Tool arguments exceed size limit (%d > %d), discarding",
                                len(tool_args),
                                _MAX_TOOL_ARGS_LEN,
                            )
                            tool_args = {}
                        else:
                            try:
                                tool_args = json.loads(tool_args)
                            except json.JSONDecodeError:
                                tool_args = {}

                    logger.debug(
                        "Executing tool: %s(%s)",
                        tool_name,
                        json.dumps(tool_args, default=str)[:200],
                    )

                    result = await self._executor.execute(tool_name, tool_args)
                    tool_result_blocks.append(result)

                    # Format result for Ollama API (tool role message)
                    result_content = result.content
                    if isinstance(result_content, list):
                        result_content = "\n".join(str(r) for r in result_content)

                    tool_results_for_api.append(
                        {
                            "role": "tool",
                            "content": str(result_content),
                        }
                    )

                # Yield UserMessage with tool results (matches Claude SDK protocol)
                yield UserMessage(content=tool_result_blocks)

                # Append assistant message and tool results to conversation
                messages.append(message)
                messages.extend(tool_results_for_api)

                # ── Convergence guard ──────────────────────────────────────
                turn_names = [(tc.get("function", {}) or {}).get("name", "") for tc in tool_calls]
                turn_sigs = [
                    (
                        (tc.get("function", {}) or {}).get("name", ""),
                        json.dumps(
                            (tc.get("function", {}) or {}).get("arguments", {}),
                            sort_keys=True,
                            default=str,
                        ),
                    )
                    for tc in tool_calls
                ]
                if any(n in _WRITERS for n in turn_names):
                    _produced_output = True
                repeated = any(s in _seen_calls for s in turn_sigs)
                _seen_calls.update(turn_sigs)
                if turn_names and all(n in _READONLY for n in turn_names):
                    _readonly_turns += 1
                else:
                    _readonly_turns = 0
                # Once the model has read enough (or is re-reading the same
                # thing) without producing output, push it to write — once.
                if (
                    not _produced_output
                    and not _nudged
                    and (_readonly_turns >= _NUDGE_AFTER or repeated)
                ):
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You have gathered enough context. Do NOT read or "
                                "search again. Produce the final required file NOW "
                                "by calling the Write tool exactly once with the "
                                "complete file content."
                            ),
                        }
                    )
                    _nudged = True

            else:
                # No tool calls — final response
                if not assistant_blocks:
                    assistant_blocks.append(TextBlock(text="(no output from Ollama)"))
                yield AssistantMessage(content=assistant_blocks)
                return

        # Max turns reached
        logger.warning(
            "OllamaAgenticProvider: max turns (%d) reached, stopping",
            self._max_turns,
        )
        yield AssistantMessage(
            content=[
                TextBlock(
                    text=f"[Reached maximum of {self._max_turns} tool-calling turns. Stopping.]"
                )
            ]
        )

    # ------------------------------------------------------------------
    # HTTP helpers (reuse pattern from OllamaProvider)
    # ------------------------------------------------------------------

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Construct the JSON request body for ``/api/chat`` with tools."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "tools": self._tool_defs,
        }
        if self._extra_options:
            body["options"] = self._extra_options
        return body

    def _http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous HTTP POST to the Ollama API.

        Runs in a thread via ``asyncio.to_thread()`` to avoid blocking.
        """
        body_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(
                f"Ollama API HTTP error {exc.code}: {exc.reason}. Response body: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama server at '{self._base_url}': {exc.reason}. "
                "Ensure Ollama is running (ollama serve) and base_url is correct."
            ) from exc

        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama API returned invalid JSON: {exc}") from exc

    def _verify_connection(self) -> None:
        """Synchronous health check via ``GET /api/tags``."""
        url = f"{self._base_url}{_PATH_TAGS}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama server at '{self._base_url}': {exc.reason}. "
                "Ensure Ollama is running (ollama serve) and base_url is correct."
            ) from exc
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Ollama server health check failed — HTTP {exc.code}: {exc.reason}."
            ) from exc

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> OllamaAgenticProvider:
        """Verify Ollama server is reachable before starting."""
        logger.debug("OllamaAgenticProvider: verifying connection to %s", self._base_url)
        await asyncio.to_thread(self._verify_connection)
        logger.debug("OllamaAgenticProvider: connection verified")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Clear pending prompt on exit."""
        self._pending_prompt = None


__all__ = ["OllamaAgenticProvider"]
