"""Shared request-header construction for the OpenAI-compatible providers."""

from __future__ import annotations


class OpenAICompatibleHeadersMixin:
    """Builds request headers shared by the plain + agentic OpenAI-compatible providers.

    Both providers assign ``_api_key`` and ``_extra_headers`` in ``__init__``;
    declared here as annotations (no value) so the mixin type-checks under
    ``mypy --strict`` without creating real class attributes.
    """

    _api_key: str | None
    _extra_headers: dict[str, str]

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, including ``Authorization`` when an API key is set."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        return headers
