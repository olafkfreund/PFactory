#!/usr/bin/env python3
"""
Graphiti Integration Helpers
============================

Helper functions for Graphiti memory system integration.
Handles checking if Graphiti is available and managing async operations.
"""

import asyncio
import copy
import logging
from pathlib import Path
from typing import Any

from . import retention, trust

logger = logging.getLogger(__name__)


def is_graphiti_memory_enabled() -> bool:
    """
    Check if Graphiti memory integration is available.

    Returns True if:
    - GRAPHITI_ENABLED is set to true/1/yes
    - A valid LLM provider is configured (OpenAI, Anthropic, Azure, or Ollama)
    - A valid embedder provider is configured (OpenAI, Voyage, Azure, or Ollama)

    See graphiti_config.py for detailed provider requirements.
    """
    try:
        from graphiti_config import is_graphiti_enabled

        return is_graphiti_enabled()
    except ImportError:
        return False


_DISCOVERY_FIELDS = (
    "patterns_found",
    "gotchas_encountered",
    "patterns_discovered",
    "gotchas_discovered",
    "recommendations",
    "file_insights",
    "files_understood",
)
_SESSION_FIELDS = ("what_worked", "what_failed", "recommendations_for_next_session")
_GOTCHA_FIELDS = ("gotchas_encountered", "gotchas_discovered")


def _mark(item: Any) -> Any:
    """Append the unverified marker to a gotcha: a string, or the ``gotcha``
    field of the extractor's dict (other fields are kept)."""
    if isinstance(item, str):
        return item if item.endswith(trust.UNVERIFIED) else item + trust.UNVERIFIED
    if isinstance(item, dict) and isinstance(item.get("gotcha"), str):
        return {**item, "gotcha": _mark(item["gotcha"])}
    return item


def gate_for_graphiti(payload: dict[str, Any], session_type: str | None = None) -> dict[str, Any]:
    """A copy of ``payload`` with the file path's rules applied (#3797).

    One shared budget ``CAPS[session_type]`` over the lesson fields, discoveries
    first (the order of ``sessions._apply_cap``), and gotchas written while the
    trust window is open carry the unverified marker. Idempotent. Adds nothing
    to what Graphiti can store: it only sends less.
    """
    out = copy.deepcopy(payload)
    budget = retention.CAPS[retention.session_type(session_type)]
    groups = (
        (out.get("discoveries"), _DISCOVERY_FIELDS),
        (out, _DISCOVERY_FIELDS + _SESSION_FIELDS),
    )
    for source, names in groups:
        if not isinstance(source, dict):
            continue
        for name in names:
            value = source.get(name)
            kept: Any
            if isinstance(value, dict):
                kept = dict(list(value.items())[: max(budget, 0)])
            elif isinstance(value, list):
                kept = value[: max(budget, 0)]
            else:
                continue
            if name in _GOTCHA_FIELDS and trust.tainted():
                kept = [_mark(item) for item in kept]
            source[name] = kept
            budget -= len(kept)
    return out


def get_graphiti_memory(spec_dir: Path, project_dir: Path | None = None):
    """
    Get a GraphitiMemory instance if available.

    Args:
        spec_dir: Spec directory
        project_dir: Project root directory (defaults to spec_dir.parent.parent)

    Returns:
        GraphitiMemory instance or None if not available
    """
    if not is_graphiti_memory_enabled():
        return None

    try:
        from graphiti_memory import GraphitiMemory

        if project_dir is None:
            project_dir = spec_dir.parent.parent
        return GraphitiMemory(spec_dir, project_dir)
    except ImportError:
        return None


def run_async(coro):
    """
    Run an async coroutine synchronously.

    Handles the case where we're already in an event loop.

    Args:
        coro: Async coroutine to run

    Returns:
        Result of the coroutine or a Future if already in event loop
    """
    try:
        loop = asyncio.get_running_loop()
        # Already in an event loop - create a task
        return asyncio.ensure_future(coro)
    except RuntimeError:
        # No event loop running - create one
        return asyncio.run(coro)


async def save_to_graphiti_async(
    spec_dir: Path,
    session_num: int,
    insights: dict[str, Any],
    project_dir: Path | None = None,
) -> bool:
    """
    Save session insights to Graphiti (async helper).

    This is called in addition to file-based storage when Graphiti is enabled.

    Args:
        spec_dir: Spec directory
        session_num: Session number
        insights: Session insights dictionary
        project_dir: Optional project directory

    Returns:
        True if save succeeded, False otherwise
    """
    graphiti = get_graphiti_memory(spec_dir, project_dir)
    if not graphiti:
        return False

    insights = gate_for_graphiti(insights)
    try:
        result = await graphiti.save_session_insights(session_num, insights)

        # Also save codebase discoveries if present
        discoveries = insights.get("discoveries", {})
        files_understood = discoveries.get("files_understood", {})
        if files_understood:
            await graphiti.save_codebase_discoveries(files_understood)

        # Save patterns
        for pattern in discoveries.get("patterns_found", []):
            await graphiti.save_pattern(pattern)

        # Save gotchas
        for gotcha in discoveries.get("gotchas_encountered", []):
            await graphiti.save_gotcha(gotcha)

        await graphiti.close()
        return result

    except Exception as e:
        logger.warning(f"Failed to save to Graphiti: {e}")
        try:
            await graphiti.close()
        except Exception:
            pass
        return False
