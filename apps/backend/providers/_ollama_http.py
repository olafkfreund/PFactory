"""Shared synchronous Ollama HTTP helpers for the plain + agentic providers.

Both ``OllamaProvider`` and ``OllamaAgenticProvider`` need identical
synchronous request/health-check helpers (run via ``asyncio.to_thread``). They
live here as a mixin so the two providers can't drift.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from typing import Any

_PATH_TAGS: str = "/api/tags"


class OllamaHTTPMixin:
    """Synchronous HTTP helpers shared by the Ollama providers.

    Both providers assign ``_base_url`` and ``_timeout`` in ``__init__``; they
    are declared here as annotations (no value) so the mixin's methods
    type-check under ``mypy --strict`` without creating real class attributes.
    """

    _base_url: str
    _timeout: int

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
        # URL is built from the configured Ollama base_url (http/https), not
        # untrusted input — the urllib scheme audit (S310) does not apply.
        req = urllib.request.Request(  # noqa: S310
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            error_body = ""
            with contextlib.suppress(Exception):
                error_body = exc.read().decode("utf-8", errors="replace")[:500]
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
        """Synchronous health check — verify the Ollama server is reachable.

        Issues a lightweight GET to ``/api/tags`` (lists available models).
        Raises ``RuntimeError`` if the server cannot be reached.  Called
        from ``__aenter__`` via ``asyncio.to_thread()``.

        Raises:
            RuntimeError: If the server is unreachable or returns an error.
        """
        url = f"{self._base_url}{_PATH_TAGS}"
        req = urllib.request.Request(url, method="GET")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
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
