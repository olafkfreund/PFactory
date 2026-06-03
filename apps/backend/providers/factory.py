"""
Unified LLM Provider Factory
==============================

Phase-aware factory that routes to the correct provider (agentic vs text-only)
based on the execution phase.

Two entry-points:

1. ``get_provider(provider_name, phase, **kwargs)`` — new, phase-aware factory.
   Automatically selects the agentic or text-only variant of a provider
   based on whether the phase needs file operations (coding, planning, etc.)
   or just text output (QA review).

2. ``get_qa_llm_provider(provider_name, **kwargs)`` — legacy factory.
   Always routes to the text-only provider variant.  Preserved for backward
   compatibility with existing ``qa/loop.py`` callers.

Usage::

    from providers.factory import get_provider

    # Agentic: coding phase with Codex → CodexAgenticProvider
    provider = get_provider("codex", phase="coding",
                            model="gpt-5.3-codex", working_dir=project_dir)

    # Text-only: QA review phase with Gemini → GeminiCLIProvider
    provider = get_provider("gemini", phase="qa",
                            model="gemini-2.5-pro", working_dir=project_dir)

    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from providers import BaseLLMProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Two-tier registry: agentic vs text-only providers
# ---------------------------------------------------------------------------

_AGENTIC_REGISTRY: dict[str, tuple[str, str]] = {
    "claude":             ("providers.claude",                       "ClaudeProvider"),
    "codex":              ("providers.codex_agentic",                "CodexAgenticProvider"),
    "gemini":             ("providers.gemini_agentic",               "GeminiAgenticProvider"),
    "ollama":             ("providers.ollama_agentic",               "OllamaAgenticProvider"),
    "openai-compatible":  ("providers.openai_compatible_agentic",    "OpenAICompatibleAgenticProvider"),
    "copilot":            ("providers.copilot_agentic",              "CopilotAgenticProvider"),
}

_TEXT_REGISTRY: dict[str, tuple[str, str]] = {
    "claude":             ("providers.claude",               "ClaudeProvider"),
    "codex":              ("providers.codex",                "CodexCLIProvider"),
    "gemini":             ("providers.gemini",               "GeminiCLIProvider"),
    "ollama":             ("providers.ollama",               "OllamaProvider"),
    "openai-compatible":  ("providers.openai_compatible",    "OpenAICompatibleProvider"),
}

# Phases that need agentic capability (file ops, code execution)
# QA needs agentic capability to update test_plan.json with qa_signoff
_AGENTIC_PHASES = {"spec", "planning", "coding", "qa_fixer", "qa"}
_TEXT_PHASES: set[str] = set()  # All phases now use agentic providers

# Human-readable aliases (normalised to canonical names)
_PROVIDER_ALIASES: dict[str, str] = {
    "claude": "claude",
    "claude-sdk": "claude",
    "anthropic": "claude",
    "codex": "codex",
    "codex-cli": "codex",
    "openai-codex": "codex",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "google": "gemini",
    "ollama": "ollama",
    "local": "ollama",
    "local-ollama": "ollama",
    # GitHub Copilot CLI (subscription-backed; runs claude-sonnet-*/gpt-5)
    "copilot": "copilot",
    "github-copilot": "copilot",
    "gh-copilot": "copilot",
    # OpenAI-compatible endpoints (LM Studio, vLLM, OpenRouter, Together, Groq, ...)
    "openai": "openai-compatible",
    "openai-api": "openai-compatible",
    "openai-compatible": "openai-compatible",
    "studio": "openai-compatible",
    "oai": "openai-compatible",
    "lm-studio": "openai-compatible",
    "lmstudio": "openai-compatible",
    "vllm": "openai-compatible",
    "openrouter": "openai-compatible",
    "together": "openai-compatible",
    "together-ai": "openai-compatible",
    "groq": "openai-compatible",
    "localai": "openai-compatible",
    "anyscale": "openai-compatible",
}


def _resolve_canonical(provider_name: str) -> str:
    """Resolve a provider name or alias to its canonical name."""
    normalised = provider_name.strip().lower()
    canonical = _PROVIDER_ALIASES.get(normalised)
    if canonical is None:
        known = sorted(_PROVIDER_ALIASES.keys())
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            f"Supported values: {known}"
        )
    return canonical


def _instantiate(module_path: str, class_name: str, **kwargs: Any) -> BaseLLMProvider:
    """Lazy-import a provider class and instantiate it."""
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Failed to import provider module '{module_path}': {exc}"
        ) from exc

    provider_cls = getattr(module, class_name)
    return provider_cls(**kwargs)


# ---------------------------------------------------------------------------
# Phase-aware factory (new)
# ---------------------------------------------------------------------------


def get_provider(provider_name: str, phase: str, **kwargs: Any) -> BaseLLMProvider:
    """Get a provider appropriate for the given phase.

    Routes to agentic or text-only provider based on phase requirements.

    Agentic phases (spec, planning, coding, qa_fixer) use providers that
    support file operations and code execution.  Text-only phases (qa) use
    lightweight providers that just return text analysis.

    Args:
        provider_name: Case-insensitive provider identifier (e.g. "codex",
            "gemini", "claude", "ollama").
        phase: Execution phase ("spec", "planning", "coding", "qa",
            "qa_fixer").
        **kwargs: Forwarded to the provider constructor.

    Returns:
        A ``BaseLLMProvider`` instance (not yet entered).

    Raises:
        ValueError: If provider_name is unrecognised, or if the provider
            doesn't support the requested phase (e.g. Ollama for coding).
    """
    canonical = _resolve_canonical(provider_name)

    if phase in _AGENTIC_PHASES:
        registry = _AGENTIC_REGISTRY
        if canonical not in registry:
            raise ValueError(
                f"Provider '{provider_name}' does not support agentic mode "
                f"needed for '{phase}' phase. Supported agentic providers: "
                f"{sorted(_AGENTIC_REGISTRY.keys())}"
            )
    else:
        registry = _TEXT_REGISTRY

    module_path, class_name = registry[canonical]

    logger.debug(
        "get_provider: phase=%r canonical=%r class=%s kwargs_keys=%s",
        phase,
        canonical,
        class_name,
        list(kwargs.keys()),
    )

    return _instantiate(module_path, class_name, **kwargs)


# ---------------------------------------------------------------------------
# Legacy factory (backward compatibility)
# ---------------------------------------------------------------------------


def get_qa_llm_provider(provider_name: str, **kwargs: Any) -> BaseLLMProvider:
    """Instantiate a text-only ``BaseLLMProvider`` by name.

    Legacy factory preserved for backward compatibility with ``qa/loop.py``.
    Always routes to the text-only provider variant.

    Raises ValueError with "Unknown QA LLM provider" message for backward
    compatibility with existing tests and callers.

    Args:
        provider_name: Case-insensitive provider identifier.
        **kwargs: Forwarded to the provider constructor.

    Returns:
        A ``BaseLLMProvider`` instance (not yet entered).
    """
    normalised = provider_name.strip().lower()
    canonical = _PROVIDER_ALIASES.get(normalised)
    if canonical is None:
        known = sorted(_PROVIDER_ALIASES.keys())
        raise ValueError(
            f"Unknown QA LLM provider: {provider_name!r}. "
            f"Supported values: {known}"
        )

    module_path, class_name = _TEXT_REGISTRY[canonical]

    logger.debug(
        "get_qa_llm_provider: canonical=%r class=%s kwargs_keys=%s",
        canonical,
        class_name,
        list(kwargs.keys()),
    )

    return _instantiate(module_path, class_name, **kwargs)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def list_providers() -> list[str]:
    """Return sorted list of all canonical provider names."""
    return sorted(set(_TEXT_REGISTRY.keys()) | set(_AGENTIC_REGISTRY.keys()))


def list_provider_aliases() -> dict[str, str]:
    """Return a copy of the alias-to-canonical mapping."""
    return dict(_PROVIDER_ALIASES)


# ---------------------------------------------------------------------------
# Tool-use fallback for text-only providers
# ---------------------------------------------------------------------------

# Preferred order for tool-capable fallback providers
_TOOL_FALLBACK_ORDER: list[str] = ["claude", "codex", "gemini"]


def get_tool_fallback_provider(
    phase: str,
    exclude: str | None = None,
    **kwargs: Any,
) -> BaseLLMProvider | None:
    """Get a tool-capable fallback provider for phases that need tool use.

    When a text-only provider (e.g. Ollama) is used for a phase that requires
    tool operations (updating files, running commands), this function returns
    a fallback provider that CAN do tool use.

    Checks availability in order: Claude → Codex → Gemini.  Returns the first
    one whose CLI is installed, skipping the ``exclude`` provider.

    Args:
        phase: Execution phase (determines agentic vs text-only routing).
        exclude: Provider name to skip (e.g. the one that already failed).
        **kwargs: Forwarded to the provider constructor (model, working_dir,
            etc.).  For Claude, ``model`` defaults to ``"sonnet"``.

    Returns:
        A ``BaseLLMProvider`` instance, or ``None`` if no fallback is available.
    """
    import shutil

    # CLI executable names for each provider
    _CLI_NAMES: dict[str, str] = {
        "claude": "claude",
        "codex": "codex",
        "gemini": "gemini",
    }

    for provider_name in _TOOL_FALLBACK_ORDER:
        if provider_name == exclude:
            continue

        cli_name = _CLI_NAMES.get(provider_name, provider_name)
        if shutil.which(cli_name) is None:
            logger.debug(
                "get_tool_fallback_provider: %s CLI not found, skipping",
                provider_name,
            )
            continue

        try:
            fallback_kwargs = dict(kwargs)
            # Set sensible defaults per provider
            if provider_name == "claude" and "model" not in fallback_kwargs:
                fallback_kwargs["model"] = "sonnet"

            provider = get_provider(provider_name, phase=phase, **fallback_kwargs)
            logger.info(
                "get_tool_fallback_provider: using %s as tool fallback for %s phase",
                provider_name,
                phase,
            )
            return provider
        except (ValueError, ImportError) as exc:
            logger.debug(
                "get_tool_fallback_provider: %s failed: %s", provider_name, exc
            )
            continue

    logger.warning("get_tool_fallback_provider: no fallback available")
    return None


__all__ = [
    "get_provider",
    "get_qa_llm_provider",
    "get_tool_fallback_provider",
    "list_providers",
    "list_provider_aliases",
]
