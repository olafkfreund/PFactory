"""
ClaudeProvider — Adapter wrapping ClaudeSDKClient
==================================================

Implements ``BaseLLMProvider`` by delegating to the existing
``ClaudeSDKClient`` created via ``core.client.create_client()``.

This adapter is the **default** QA LLM provider and preserves full
backward compatibility.  All existing QA reviewer / fixer behaviour
is unchanged; the only difference is that the client is now reachable
through the ``BaseLLMProvider`` interface.

Usage::

    from pathlib import Path
    from qa.providers.claude import ClaudeProvider

    provider = ClaudeProvider(
        project_dir=project_dir,
        spec_dir=spec_dir,
        model="claude-opus-4-5",
        agent_type="qa_reviewer",
        max_thinking_tokens=16000,
    )
    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from providers import BaseLLMProvider

logger = logging.getLogger(__name__)


class ClaudeProvider(BaseLLMProvider):
    """
    QA LLM provider backed by the Claude Agent SDK (``ClaudeSDKClient``).

    Acts as a thin adapter: construction mirrors ``create_client()``
    and all method calls are forwarded to the underlying SDK client.

    The ``ClaudeSDKClient`` is created eagerly in ``__init__`` (just
    like the existing ``create_client()`` call in ``loop.py``) so that
    any configuration errors surface before the ``async with`` block.

    Args:
        project_dir: Root directory for the project (working directory).
        spec_dir: Directory containing the spec (for settings file).
        model: Claude model identifier (e.g. ``"claude-opus-4-5"``).
        agent_type: Agent role key from ``AGENT_CONFIGS``
                    (default: ``"qa_reviewer"``).
        max_thinking_tokens: Extended-thinking token budget, or ``None``
                             to disable extended thinking.
        output_format: Optional structured output format dict.
        agents: Optional sub-agent definitions dict.
        betas: Optional list of SDK beta header strings.
        effort_level: Optional effort level for adaptive thinking models.
        fast_mode: Enable fast mode for Opus 4.6.
        thinking_level: Optional thinking level ("none"/"low"/"medium"/"high"). When
                       provided, opts in to the SDK-native `thinking` parameter via
                       phase_config.thinking_config_for() — Opus 4.7 becomes
                       {"type": "adaptive"}, other models map to
                       {"type": "enabled", "budget_tokens": N}. None preserves
                       legacy max_thinking_tokens behaviour.
    """

    def __init__(
        self,
        project_dir: Path | None = None,
        spec_dir: Path | None = None,
        model: str = "sonnet",
        agent_type: str = "qa_reviewer",
        max_thinking_tokens: int | None = None,
        output_format: dict | None = None,
        agents: dict | None = None,
        betas: list[str] | None = None,
        effort_level: str | None = None,
        fast_mode: bool = False,
        thinking_level: str | None = None,
        working_dir: Path | None = None,  # Alias for project_dir (compat with other providers)
        **_kwargs: Any,  # Ignore unknown kwargs from factory
    ) -> None:
        # Accept working_dir as alias for project_dir
        if project_dir is None and working_dir is not None:
            project_dir = working_dir
        if spec_dir is None and project_dir is not None:
            spec_dir = project_dir
        # Import here to avoid circular imports (core.client imports many things)
        from core.client import create_client

        self._sdk_client: ClaudeSDKClient = create_client(
            project_dir=project_dir,
            spec_dir=spec_dir,
            model=model,
            agent_type=agent_type,
            max_thinking_tokens=max_thinking_tokens,
            output_format=output_format,
            agents=agents,
            betas=betas,
            effort_level=effort_level,
            fast_mode=fast_mode,
            thinking_level=thinking_level,
        )
        logger.debug(
            "ClaudeProvider created",
            extra={
                "model": model,
                "agent_type": agent_type,
                "max_thinking_tokens": max_thinking_tokens,
            },
        )

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    async def query(self, prompt: str) -> None:
        """Forward the prompt to the underlying ``ClaudeSDKClient``."""
        await self._sdk_client.query(prompt)

    def receive_response(self) -> AsyncIterator[Any]:
        """Return the SDK client's response stream directly."""
        return self._sdk_client.receive_response()

    async def __aenter__(self) -> ClaudeProvider:
        """Enter the SDK client context manager and return *self*."""
        await self._sdk_client.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the SDK client context manager."""
        await self._sdk_client.__aexit__(exc_type, exc_val, exc_tb)


__all__ = ["ClaudeProvider"]
