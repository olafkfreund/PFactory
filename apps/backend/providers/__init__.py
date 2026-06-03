"""
LLM Provider Abstraction Layer
================================

Top-level package for all LLM provider adapters.  Generalised from the
original ``qa/providers/`` package to support any execution phase — not
just QA review.

Defines the minimal interface (``BaseLLMProvider``) that any LLM backend must
satisfy to replace ``ClaudeSDKClient`` inside agent sessions.

Minimal interface
-----------------
Callers consume exactly **two methods** plus the **async context manager**
protocol:

1. ``query(prompt: str) -> None``
   Send the initial prompt and start the response stream.

2. ``receive_response() -> AsyncIterator[Any]``
   Stream back structured message objects.  Each object is inspected
   *only* via ``type(msg).__name__`` string comparisons — no ``isinstance``
   calls — so adapters must yield objects whose class names match exactly:

   Top-level: ``AssistantMessage``, ``UserMessage``
   Blocks:    ``TextBlock``, ``ToolUseBlock``, ``ToolResultBlock``

3. Async context manager (``__aenter__`` / ``__aexit__``)
   Callers always wrap providers in ``async with provider:`` for resource
   management.

Package layout
--------------
    providers/
        __init__.py         — BaseLLMProvider ABC (this file)
        types.py            — Shared message-protocol wrapper classes
        claude.py           — ClaudeProvider   (wraps ClaudeSDKClient)
        codex.py            — CodexCLIProvider  (Codex CLI text-only)
        codex_agentic.py    — CodexAgenticProvider (Codex CLI full-auto)
        gemini.py           — GeminiCLIProvider (Gemini CLI text-only)
        gemini_agentic.py   — GeminiAgenticProvider (Gemini CLI sandbox)
        ollama.py           — OllamaProvider   (local Ollama text-only adapter)
        ollama_agentic.py   — OllamaAgenticProvider (native tool calling)
        factory.py          — Unified get_provider() + legacy get_qa_llm_provider()

Usage::

    from providers.factory import get_provider

    provider = get_provider("codex", phase="coding", model="gpt-5.3-codex",
                            working_dir=project_dir)
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from .types import (
    AssistantMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class BaseLLMProvider(ABC):
    """
    Minimal interface every LLM provider adapter must satisfy.

    Concrete implementations live in the sibling modules:
    - ``providers.claude``          — wraps ClaudeSDKClient (default)
    - ``providers.codex``           — Codex CLI text-only
    - ``providers.codex_agentic``   — Codex CLI full-auto (agentic)
    - ``providers.gemini``          — Gemini CLI text-only
    - ``providers.gemini_agentic``  — Gemini CLI sandbox (agentic)
    - ``providers.ollama``          — local Ollama / OpenAI-compatible
    """

    @abstractmethod
    async def query(self, prompt: str) -> None:
        """Send a prompt to the LLM to start a response stream."""

    @abstractmethod
    def receive_response(self) -> AsyncIterator[Any]:
        """Return an async iterable of message objects produced by the LLM."""

    @abstractmethod
    async def __aenter__(self) -> BaseLLMProvider:
        """Enter the provider context (connect, initialise session, etc.)."""

    @abstractmethod
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the provider context (disconnect, cleanup, etc.)."""


# ---------------------------------------------------------------------------
# Factory (imported after BaseLLMProvider to avoid circular imports)
# ---------------------------------------------------------------------------

from .factory import (  # noqa: E402
    get_provider,
    get_qa_llm_provider,
    list_provider_aliases,
    list_providers,
)

# ---------------------------------------------------------------------------
# Re-export public symbols
# ---------------------------------------------------------------------------

__all__ = [
    # Abstract base
    "BaseLLMProvider",
    # Message protocol types
    "AssistantMessage",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "UserMessage",
    # Factory
    "get_provider",
    "get_qa_llm_provider",
    "list_providers",
    "list_provider_aliases",
]
