"""
OllamaProvider — Adapter for local Ollama / OpenAI-compatible LLMs
===================================================================

Implements ``BaseLLMProvider`` by calling the **Ollama REST API**
(``POST /api/chat``) using Python's standard-library ``urllib`` — no extra
dependencies required.

The adapter sends the QA prompt as a single user message to the locally
running Ollama server, waits for the (non-streaming) response, and wraps
the plain-text reply in the message-protocol types expected by
``reviewer.py`` and ``fixer.py``.

Since the Ollama API does not expose a tool-use protocol, no
``ToolUseBlock`` or ``UserMessage`` objects are ever produced.  The QA
reviewer will receive the model's complete analysis as plain text.

Usage::

    from pathlib import Path
    from qa.providers.ollama import OllamaProvider

    provider = OllamaProvider(
        model="llama3.2",                    # Ollama model tag
        base_url="http://localhost:11434",   # Ollama server base URL
        timeout=300,                         # HTTP timeout in seconds
        extra_options={"temperature": 0},    # Ollama model options
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...

API call shape::

    POST <base_url>/api/chat
    Content-Type: application/json

    {
        "model": "<model>",
        "messages": [{"role": "user", "content": "<prompt>"}],
        "stream": false,
        "options": { ...extra_options... }
    }

The prompt is sent as the ``content`` of a single ``user`` message.  The
``stream`` flag is set to ``false`` so that the server waits until the
complete response is ready before returning, keeping the adapter simple and
without requiring incremental JSON parsing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from providers import BaseLLMProvider
from providers.types import AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults (overridable per-instance)
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL: str = "http://localhost:11434"
_DEFAULT_MODEL: str = "llama3.2"
_DEFAULT_TIMEOUT: int = 300  # seconds

# Ollama REST endpoints (relative paths)
_PATH_CHAT: str = "/api/chat"
_PATH_TAGS: str = "/api/tags"


class OllamaProvider(BaseLLMProvider):
    """
    QA LLM provider backed by the Ollama REST API.

    The adapter calls the local Ollama server's ``/api/chat`` endpoint,
    sends the QA prompt as a single ``user`` message, and yields the
    assistant's reply as a single ``AssistantMessage`` containing one
    ``TextBlock``.

    HTTP calls are made via Python's standard-library ``urllib`` inside
    ``asyncio.to_thread()`` so the event loop is never blocked.

    Because the Ollama API does not expose a tool-use protocol, no
    ``ToolUseBlock`` or ``UserMessage`` objects are ever produced.

    Args:
        model: Ollama model tag to use (e.g. ``"llama3.2"``,
               ``"codellama:13b"``).  Must already be pulled on the
               local Ollama server.
        base_url: Base URL of the Ollama server.  Defaults to
                  ``"http://localhost:11434"``.  Override for remote
                  Ollama servers or custom ports.
        timeout: Maximum number of seconds to wait for the HTTP request
                 to complete before raising ``asyncio.TimeoutError``.
                 Defaults to 300 (5 minutes).
        extra_options: Optional dict of Ollama model parameters forwarded
                       in the ``"options"`` field of the request body
                       (e.g. ``{"temperature": 0, "num_predict": 4096}``).
                       See the Ollama documentation for supported keys.
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        extra_options: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._extra_options: dict[str, Any] = extra_options or {}
        # Default to 32K context for QA review of large codebases
        if "num_ctx" not in self._extra_options:
            self._extra_options["num_ctx"] = 32768
        self._pending_prompt: str | None = None

        logger.debug(
            "OllamaProvider created",
            extra={
                "model": model,
                "base_url": base_url,
                "timeout": timeout,
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
            "OllamaProvider: prompt stored (length=%d)", len(prompt)
        )

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that calls the Ollama REST API.

        The generator sends the stored prompt to ``/api/chat``, captures
        the full response, and yields a single ``AssistantMessage``.

        Returns:
            An ``AsyncGenerator`` that yields one ``AssistantMessage``
            containing one ``TextBlock`` with the full model response.

        Raises:
            RuntimeError: If the Ollama server returns an HTTP error or
                          an unexpected JSON structure.
            asyncio.TimeoutError: If the HTTP request exceeds
                                  ``self._timeout`` seconds without
                                  completing.
        """
        return self._run_ollama()

    async def _run_ollama(self) -> AsyncGenerator[Any, None]:
        """Async generator: call the Ollama API and yield the response message.

        Yields:
            ``AssistantMessage(content=[TextBlock(text=<response>)])``
        """
        if not self._pending_prompt:
            logger.warning(
                "OllamaProvider.receive_response() called before query() — "
                "no prompt to send"
            )
            return

        payload = self._build_payload(self._pending_prompt)
        url = f"{self._base_url}{_PATH_CHAT}"

        logger.debug(
            "OllamaProvider: sending request url=%r model=%r", url, self._model
        )

        try:
            response_data = await asyncio.wait_for(
                asyncio.to_thread(self._http_post, url, payload),
                timeout=float(self._timeout),
            )
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"Ollama API request timed out after {self._timeout}s. "
                "Increase timeout= or reduce prompt size."
            )

        response_text = self._extract_content(response_data)

        logger.debug(
            "OllamaProvider: response received content_len=%d",
            len(response_text),
        )

        yield AssistantMessage(content=[TextBlock(text=response_text)])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, prompt: str) -> dict[str, Any]:
        """Construct the JSON request body for ``/api/chat``.

        Args:
            prompt: The full QA prompt string.

        Returns:
            A dict ready to be serialised to JSON and sent to Ollama.
        """
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if self._extra_options:
            body["options"] = self._extra_options
        return body

    def _http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous HTTP POST to the Ollama API.

        This is intentionally synchronous so it can be run in a thread
        via ``asyncio.to_thread()`` without blocking the event loop.

        Args:
            url: Full URL to POST to (e.g. ``"http://localhost:11434/api/chat"``).
            payload: JSON-serialisable request body dict.

        Returns:
            Parsed JSON response as a Python dict.

        Raises:
            RuntimeError: On HTTP errors or JSON decode failures.
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
                f"Ollama API HTTP error {exc.code}: {exc.reason}. "
                f"Response body: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama server at '{self._base_url}': {exc.reason}. "
                "Ensure Ollama is running (ollama serve) and base_url is correct."
            ) from exc

        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama API returned invalid JSON: {exc}"
            ) from exc

    @staticmethod
    def _extract_content(response_data: dict[str, Any]) -> str:
        """Extract the assistant reply text from an Ollama ``/api/chat`` response.

        Expected response shape::

            {
                "model": "...",
                "message": {"role": "assistant", "content": "..."},
                "done": true,
                ...
            }

        Args:
            response_data: Parsed JSON response from the Ollama API.

        Returns:
            The assistant's reply as a plain string.

        Raises:
            RuntimeError: If the expected keys are absent from the response.
        """
        message = response_data.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(
                "Ollama API response missing 'message' field. "
                f"Got keys: {list(response_data.keys())}"
            )

        content = message.get("content")
        if content is None:
            raise RuntimeError(
                "Ollama API response 'message' missing 'content' field. "
                f"Got keys: {list(message.keys())}"
            )

        return str(content).strip() or "(no output from Ollama)"

    def _verify_connection(self) -> None:
        """Synchronous health check — verify the Ollama server is reachable.

        Issues a lightweight GET to ``/api/tags`` (lists available models).
        Raises ``RuntimeError`` if the server cannot be reached.  Called
        from ``__aenter__`` via ``asyncio.to_thread()``.

        Raises:
            RuntimeError: If the server is unreachable or returns an error.
        """
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

    async def __aenter__(self) -> OllamaProvider:
        """Verify the Ollama server is reachable before the QA session starts.

        Issues a lightweight ``GET /api/tags`` health check so that
        connection problems are reported immediately rather than mid-session.

        Raises:
            RuntimeError: If the Ollama server cannot be reached.
        """
        logger.debug(
            "OllamaProvider: verifying connection to %s", self._base_url
        )
        await asyncio.to_thread(self._verify_connection)
        logger.debug("OllamaProvider: connection verified")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Clear the pending prompt on context exit."""
        self._pending_prompt = None


__all__ = ["OllamaProvider"]
