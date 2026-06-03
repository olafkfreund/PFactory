"""
OpenAICompatibleAgenticProvider — Agentic OpenAI-compatible provider
=====================================================================

Multi-turn conversation loop using OpenAI's standard tool-calling protocol
(``tools`` in the request, ``tool_calls`` in the response).  Works against
any server that implements ``POST /v1/chat/completions`` with the OpenAI
function-calling shape:

* OpenAI itself
* OpenRouter
* Together AI
* Groq
* LM Studio (recent versions)
* vLLM (with tool-calling support)
* LocalAI
* Ollama's OpenAI shim

Implements its own tool execution loop using the reusable
``tools.ToolExecutor`` — same approach as ``OllamaAgenticProvider``.

Protocol differences vs Ollama's native shape:

================  ==========================  =========================
field             OpenAI                       Ollama
================  ==========================  =========================
endpoint          /v1/chat/completions         /api/chat
auth              Authorization: Bearer …      none (typically local)
response          choices[0].message           message
tool_calls.args   JSON **string**              dict (sometimes string)
tool_calls.id     required, echoed back        not used
tool result msg   {role: "tool",               {role: "tool",
                   tool_call_id: <id>,          content: "..."}
                   content: "..."}
options           top-level (temperature,…)    nested "options" key
================  ==========================  =========================
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

_DEFAULT_BASE_URL: str = "https://api.openai.com"
_DEFAULT_MODEL: str = "gpt-4o-mini"
_DEFAULT_TIMEOUT: int = 600
_DEFAULT_MAX_TURNS: int = 25
_DEFAULT_TOOL_NAMES: list[str] = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

_PATH_CHAT: str = "/v1/chat/completions"
_PATH_MODELS: str = "/v1/models"
_MAX_TOOL_ARGS_LEN: int = 50_000  # 50 KB safety limit


class OpenAICompatibleAgenticProvider(BaseLLMProvider):
    """Agentic OpenAI-compatible provider with native tool calling.

    Sends prompts to any server speaking ``/v1/chat/completions`` with the
    OpenAI ``tools``/``tool_calls`` schema.  Executes tool calls locally via
    ``ToolExecutor`` and feeds results back into the conversation.

    Args:
        model: Model identifier (e.g. ``"gpt-4o"``, ``"qwen2.5-coder-32b"``,
            ``"llama-3.3-70b-instruct"``).  Must support tool calling.
        base_url: Server base URL (no trailing ``/v1``).
        api_key: Optional bearer token; skip for local servers.
        timeout: Per-request timeout in seconds (agentic needs more time).
        working_dir: Project directory — security boundary for tool execution.
        max_turns: Maximum conversation turns before halting (loop safety).
        tool_names: Tool names to expose to the model.
        extra_headers: Additional headers (e.g. OpenRouter routing hints).
        extra_options: Additional request body fields (temperature, max_tokens,
            top_p, etc.).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: Path | str = Path("."),
        max_turns: int = _DEFAULT_MAX_TURNS,
        tool_names: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or None
        self._timeout = timeout
        self._working_dir = Path(working_dir).resolve()
        self._max_turns = max_turns
        self._extra_headers: dict[str, str] = extra_headers or {}
        self._extra_options: dict[str, Any] = extra_options or {}
        self._pending_prompt: str | None = None

        effective_tools = tool_names or _DEFAULT_TOOL_NAMES
        self._tool_defs = get_tool_definitions(effective_tools)
        self._executor = ToolExecutor(
            working_dir=self._working_dir,
            bash_timeout=min(timeout, 120),
        )

        logger.debug(
            "OpenAICompatibleAgenticProvider created: model=%s base_url=%s "
            "tools=%s max_turns=%d has_api_key=%s",
            model,
            self._base_url,
            effective_tools,
            max_turns,
            bool(self._api_key),
        )

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    async def query(self, prompt: str) -> None:
        self._pending_prompt = prompt
        logger.debug(
            "OpenAICompatibleAgenticProvider: prompt stored (length=%d)",
            len(prompt),
        )

    def receive_response(self) -> AsyncIterator[Any]:
        return self._run_agentic_loop()

    async def _run_agentic_loop(self) -> AsyncGenerator[Any, None]:
        """Multi-turn agentic loop.

        1. Send messages + tool definitions to ``/v1/chat/completions``
        2. If response has ``tool_calls``: execute tools, append results, loop
        3. Otherwise: yield final text and stop
        4. Safety: halt after ``max_turns`` iterations
        """
        if not self._pending_prompt:
            logger.warning(
                "OpenAICompatibleAgenticProvider.receive_response() called "
                "before query()"
            )
            return

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._pending_prompt},
        ]

        for turn in range(self._max_turns):
            logger.debug(
                "OpenAICompatibleAgenticProvider: turn %d/%d",
                turn + 1,
                self._max_turns,
            )

            payload = self._build_payload(messages)
            url = f"{self._base_url}{_PATH_CHAT}"

            try:
                response_data = await asyncio.wait_for(
                    asyncio.to_thread(self._http_post, url, payload),
                    timeout=float(self._timeout),
                )
            except asyncio.TimeoutError:
                yield AssistantMessage(content=[TextBlock(
                    text=(
                        f"[OpenAI-compatible request timed out after "
                        f"{self._timeout}s on turn {turn + 1}]"
                    )
                )])
                return

            # OpenAI shape: choices[0].message
            choices = response_data.get("choices")
            if not isinstance(choices, list) or not choices:
                err = response_data.get("error")
                detail = err or response_data
                yield AssistantMessage(content=[TextBlock(
                    text=f"[OpenAI-compatible API returned no choices: {detail}]"
                )])
                return

            message = choices[0].get("message", {}) if isinstance(
                choices[0], dict
            ) else {}
            content_text = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls")

            assistant_blocks: list[Any] = []
            if content_text:
                assistant_blocks.append(TextBlock(text=content_text))

            if tool_calls:
                # Build the assistant turn blocks (text + ToolUseBlocks)
                normalized_calls: list[dict[str, Any]] = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    tool_name = fn.get("name") or ""
                    tool_args = self._parse_tool_args(fn.get("arguments"))
                    tool_call_id = tc.get("id") or f"call_{turn}_{len(normalized_calls)}"

                    normalized_calls.append({
                        "id": tool_call_id,
                        "name": tool_name,
                        "args": tool_args,
                        # Gemini 3.x returns a reasoning signature per tool call
                        # in extra_content.google.thought_signature and REQUIRES
                        # it to be echoed back on the next turn, or the request
                        # 400s. Other endpoints omit it (harmless passthrough).
                        "extra_content": tc.get("extra_content"),
                    })
                    assistant_blocks.append(
                        ToolUseBlock(name=tool_name, input=tool_args)
                    )

                yield AssistantMessage(content=assistant_blocks)

                # Execute tools and collect results
                tool_result_blocks: list[Any] = []
                tool_messages: list[dict[str, Any]] = []

                for call in normalized_calls:
                    logger.debug(
                        "Executing tool: %s(%s)",
                        call["name"],
                        json.dumps(call["args"], default=str)[:200],
                    )
                    result = await self._executor.execute(
                        call["name"], call["args"]
                    )
                    tool_result_blocks.append(result)

                    result_content = result.content
                    if isinstance(result_content, list):
                        result_content = "\n".join(str(r) for r in result_content)

                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": str(result_content),
                    })

                yield UserMessage(content=tool_result_blocks)

                # Append the assistant message (with tool_calls) and tool replies
                # to the next request.  We must echo the same tool_call_id values
                # the model returned so the server can correlate them.
                assistant_msg_for_api: dict[str, Any] = {
                    "role": "assistant",
                    "content": content_text or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call["args"]),
                            },
                            # Echo the provider's reasoning signature (Gemini 3.x
                            # requires it; absent for other endpoints).
                            **(
                                {"extra_content": call["extra_content"]}
                                if call.get("extra_content")
                                else {}
                            ),
                        }
                        for call in normalized_calls
                    ],
                }
                messages.append(assistant_msg_for_api)
                messages.extend(tool_messages)

            else:
                # No tool calls — final response
                if not assistant_blocks:
                    assistant_blocks.append(
                        TextBlock(text="(no output from server)")
                    )
                yield AssistantMessage(content=assistant_blocks)
                return

        logger.warning(
            "OpenAICompatibleAgenticProvider: max turns (%d) reached, stopping",
            self._max_turns,
        )
        yield AssistantMessage(content=[TextBlock(
            text=(
                f"[Reached maximum of {self._max_turns} tool-calling turns. "
                "Stopping.]"
            )
        )])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        """Parse the ``arguments`` field from a tool_call.

        Per OpenAI spec ``arguments`` is a JSON string, but real-world
        servers/models also return:
        * already-parsed dicts
        * malformed JSON
        * truncated JSON
        Be defensive: return ``{}`` on any failure.
        """
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            if len(raw) > _MAX_TOOL_ARGS_LEN:
                logger.warning(
                    "Tool arguments exceed size limit (%d > %d), discarding",
                    len(raw),
                    _MAX_TOOL_ARGS_LEN,
                )
                return {}
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                logger.warning("Tool arguments are not valid JSON; discarding")
                return {}
        return {}

    def _build_payload(
        self, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Construct the JSON body for ``/v1/chat/completions``."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "tools": self._tool_defs,
        }
        # Default to deterministic output for code-generation tasks
        body.setdefault("temperature", 0)
        body.update(self._extra_options)
        return body

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        return headers

    def _http_post(
        self, url: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        body_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=self._build_headers(),
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
                f"OpenAI-compatible API HTTP error {exc.code}: {exc.reason}. "
                f"Response body: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach OpenAI-compatible server at '{self._base_url}': "
                f"{exc.reason}."
            ) from exc

        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenAI-compatible API returned invalid JSON: {exc}"
            ) from exc

    def _verify_connection(self) -> None:
        """Health check via ``GET /v1/models``.  Treat 404/405 as 'reachable'."""
        url = f"{self._base_url}{_PATH_MODELS}"
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                logger.debug(
                    "OpenAICompatibleAgenticProvider: /v1/models returned %d "
                    "(server reachable but endpoint not implemented)",
                    exc.code,
                )
                return
            raise RuntimeError(
                f"OpenAI-compatible server health check failed — "
                f"HTTP {exc.code}: {exc.reason}."
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach OpenAI-compatible server at '{self._base_url}': "
                f"{exc.reason}."
            ) from exc

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> OpenAICompatibleAgenticProvider:
        logger.debug(
            "OpenAICompatibleAgenticProvider: verifying connection to %s",
            self._base_url,
        )
        await asyncio.to_thread(self._verify_connection)
        logger.debug("OpenAICompatibleAgenticProvider: connection verified")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._pending_prompt = None


__all__ = ["OpenAICompatibleAgenticProvider"]
