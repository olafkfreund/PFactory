"""
OpenAICompatibleProvider — Adapter for any OpenAI-compatible chat endpoint
=========================================================================

Implements ``BaseLLMProvider`` by calling the standard OpenAI
``POST /v1/chat/completions`` endpoint exposed by services such as:

* OpenAI (api.openai.com)
* LM Studio
* vLLM
* OpenRouter
* Together AI
* Groq
* LocalAI
* Anyscale Endpoints
* Ollama's OpenAI-compatible shim
* Any FastAPI/Flask service that speaks the OpenAI protocol

Uses Python's standard-library ``urllib`` — no extra dependencies.

The adapter sends the prompt as a single user message, waits for the
(non-streaming) response, and wraps the plain-text reply in the
message-protocol types expected by the rest of the codebase.

Usage::

    from providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        model="gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key="sk-...",
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...

API call shape::

    POST <base_url>/v1/chat/completions
    Authorization: Bearer <api_key>     (optional)
    Content-Type: application/json

    {
        "model": "<model>",
        "messages": [{"role": "user", "content": "<prompt>"}],
        "stream": false,
        "temperature": 0,
        "max_tokens": 4096
    }

Response::

    {
        "choices": [
            {"message": {"role": "assistant", "content": "..."}}
        ]
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from providers import BaseLLMProvider
from providers.types import AssistantMessage, TextBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL: str = "https://api.openai.com"
_DEFAULT_MODEL: str = "gpt-4o-mini"
_DEFAULT_TIMEOUT: int = 300  # seconds

_PATH_CHAT: str = "/v1/chat/completions"
_PATH_MODELS: str = "/v1/models"


class OpenAICompatibleProvider(BaseLLMProvider):
    """LLM provider backed by any OpenAI-compatible ``/v1/chat/completions`` endpoint.

    The adapter sends the prompt as a single ``user`` message and yields the
    assistant's reply as a single ``AssistantMessage`` containing one
    ``TextBlock``.

    Args:
        model: Model identifier for the target server (e.g. ``"gpt-4o-mini"``,
            ``"llama-3.3-70b-instruct"``, ``"mistral-large"``).
        base_url: Base URL of the server (no trailing ``/v1``).  The provider
            appends ``/v1/chat/completions`` itself.
        api_key: Optional bearer token.  Skip or pass an empty string when
            targeting local servers (LM Studio, vLLM) that don't require auth.
        timeout: Maximum seconds to wait for the HTTP response.  Defaults
            to 300 (5 minutes).
        extra_headers: Optional dict of additional headers to send with each
            request (e.g. ``{"HTTP-Referer": "..."}`` for OpenRouter).
        extra_options: Optional dict merged into the JSON body
            (e.g. ``{"temperature": 0.2, "max_tokens": 8192,
            "top_p": 1, "frequency_penalty": 0}``).
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
        extra_headers: dict[str, str] | None = None,
        extra_options: dict[str, Any] | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or None  # treat empty string as None
        self._timeout = timeout
        self._extra_headers: dict[str, str] = extra_headers or {}
        self._extra_options: dict[str, Any] = extra_options or {}
        self._pending_prompt: str | None = None

        logger.debug(
            "OpenAICompatibleProvider created",
            extra={
                "model": model,
                "base_url": self._base_url,
                "timeout": timeout,
                "has_api_key": bool(self._api_key),
            },
        )

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    async def query(self, prompt: str) -> None:
        """Store the prompt for execution when ``receive_response()`` is called."""
        self._pending_prompt = prompt
        logger.debug(
            "OpenAICompatibleProvider: prompt stored (length=%d)", len(prompt)
        )

    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async generator that calls ``/v1/chat/completions``."""
        return self._run_request()

    async def _run_request(self) -> AsyncGenerator[Any, None]:
        """Async generator: call the chat endpoint and yield the assistant message.

        Yields:
            ``AssistantMessage(content=[TextBlock(text=<response>)])``
        """
        if not self._pending_prompt:
            logger.warning(
                "OpenAICompatibleProvider.receive_response() called before "
                "query() — no prompt to send"
            )
            return

        payload = self._build_payload(self._pending_prompt)
        url = f"{self._base_url}{_PATH_CHAT}"

        logger.debug(
            "OpenAICompatibleProvider: POST %s model=%r", url, self._model
        )

        try:
            response_data = await asyncio.wait_for(
                asyncio.to_thread(self._http_post, url, payload),
                timeout=float(self._timeout),
            )
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"OpenAI-compatible API request timed out after {self._timeout}s. "
                "Increase timeout= or reduce prompt size."
            )

        response_text = self._extract_content(response_data)

        logger.debug(
            "OpenAICompatibleProvider: response received content_len=%d",
            len(response_text),
        )

        yield AssistantMessage(content=[TextBlock(text=response_text)])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, prompt: str) -> dict[str, Any]:
        """Construct the JSON request body for ``/v1/chat/completions``."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        # Sensible default — most callers want deterministic output for QA-style use
        body.setdefault("temperature", 0)
        body.update(self._extra_options)
        return body

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, including ``Authorization`` when an API key is set."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        return headers

    def _http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous HTTP POST — run inside ``asyncio.to_thread()``.

        Raises:
            RuntimeError: On HTTP errors or JSON decode failures.
        """
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
                f"{exc.reason}. Verify base_url and that the server is running."
            ) from exc

        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenAI-compatible API returned invalid JSON: {exc}"
            ) from exc

    @staticmethod
    def _extract_content(response_data: dict[str, Any]) -> str:
        """Extract the assistant reply text from an OpenAI-shape response.

        Expected response shape::

            {
                "choices": [
                    {"message": {"role": "assistant", "content": "..."}}
                ]
            }

        Raises:
            RuntimeError: If the expected keys are absent.
        """
        choices = response_data.get("choices")
        if not isinstance(choices, list) or not choices:
            # Some servers wrap errors in {"error": {...}} — surface that.
            err = response_data.get("error")
            if err:
                raise RuntimeError(f"OpenAI-compatible API returned error: {err}")
            raise RuntimeError(
                f"OpenAI-compatible API response missing 'choices'. "
                f"Got keys: {list(response_data.keys())}"
            )

        first = choices[0]
        if not isinstance(first, dict):
            raise RuntimeError("OpenAI-compatible API: 'choices[0]' is not an object")

        message = first.get("message")
        if not isinstance(message, dict):
            raise RuntimeError(
                "OpenAI-compatible API response missing 'choices[0].message'"
            )

        content = message.get("content")
        if content is None:
            raise RuntimeError(
                "OpenAI-compatible API response missing 'choices[0].message.content'"
            )

        return str(content).strip() or "(no output from server)"

    # ------------------------------------------------------------------
    # Health check (used by __aenter__)
    # ------------------------------------------------------------------

    def _verify_connection(self) -> None:
        """Synchronous health check — verify the server is reachable.

        Issues a lightweight ``GET /v1/models``.  Some compat servers don't
        implement this endpoint (return 404), in which case we accept the
        404 as proof the server is reachable but ``/v1/models`` is unsupported.

        Raises:
            RuntimeError: If the server is unreachable entirely.
        """
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
            # 404/405 → endpoint exists, /v1/models just not implemented. OK.
            if exc.code in (404, 405):
                logger.debug(
                    "OpenAICompatibleProvider: /v1/models returned %d "
                    "(endpoint may not be implemented; continuing)",
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

    async def __aenter__(self) -> OpenAICompatibleProvider:
        logger.debug(
            "OpenAICompatibleProvider: verifying connection to %s",
            self._base_url,
        )
        await asyncio.to_thread(self._verify_connection)
        logger.debug("OpenAICompatibleProvider: connection verified")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._pending_prompt = None


__all__ = ["OpenAICompatibleProvider"]
