"""
Unified LLM Provider Factory
==============================

Factory that routes a provider name to its agentic provider class. All
execution phases use agentic providers (file ops + code execution); the
former text-only tier has been removed.

Entry-point:

    ``get_provider(provider_name, phase, **kwargs)`` — resolves the provider
    name/alias and instantiates the matching agentic provider. ``phase`` is
    retained for logging and tool-fallback routing.

Usage::

    from providers.factory import get_provider

    # Coding phase with Codex → CodexAgenticProvider
    provider = get_provider("codex", phase="coding",
                            model="gpt-5.3-codex", working_dir=project_dir)

    async with provider:
        await provider.query(prompt)
        async for msg in provider.receive_response():
            ...
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from providers import BaseLLMProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider registry (all agentic)
# ---------------------------------------------------------------------------

_AGENTIC_REGISTRY: dict[str, tuple[str, str]] = {
    "claude": ("providers.claude", "ClaudeProvider"),
    "codex": ("providers.codex_agentic", "CodexAgenticProvider"),
    "gemini": ("providers.gemini_agentic", "GeminiAgenticProvider"),
    "ollama": ("providers.ollama_agentic", "OllamaAgenticProvider"),
    "openai-compatible": (
        "providers.openai_compatible_agentic",
        "OpenAICompatibleAgenticProvider",
    ),
    "copilot": ("providers.copilot_agentic", "CopilotAgenticProvider"),
    # GitHub Models (epic #87 / #88) — free OpenAI-compatible inference via
    # models.github.ai, authed by GITHUB_TOKEN. Routes through the existing
    # openai-compatible backend; defaults injected in get_provider().
    "github-models": (
        "providers.openai_compatible_agentic",
        "OpenAICompatibleAgenticProvider",
    ),
    # Ollama Cloud (issue #94) — hosted OpenAI-compatible inference at
    # https://ollama.com/v1, authed by OLLAMA_API_KEY. Reachable from the
    # cluster (unlike host-local Ollama). Routes through the openai-compatible
    # backend; defaults injected in get_provider().
    "ollama-cloud": (
        "providers.openai_compatible_agentic",
        "OpenAICompatibleAgenticProvider",
    ),
}

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
    # GitHub Models (epic #87 / #88). NOTE: deliberately do NOT alias bare
    # "github" — that would shadow the gh/GitHub API integration used across
    # runners/github/ and the gh CLI wrapper paths.
    "github-models": "github-models",
    "gh-models": "github-models",
    "githubmodels": "github-models",
    # Ollama Cloud (issue #94). NOTE: deliberately separate from bare "ollama"
    # (host-local, unauthenticated) — cloud REQUIRES OLLAMA_API_KEY and routes
    # to https://ollama.com, not localhost:11434.
    "ollama-cloud": "ollama-cloud",
    "ollama_cloud": "ollama-cloud",
    "ollamacloud": "ollama-cloud",
    "ollama.com": "ollama-cloud",
}


def _apply_github_models_defaults(kwargs: dict[str, Any]) -> None:
    """Pre-configure GitHub Models routing onto the openai-compatible backend.

    Injects the models.github.ai endpoint + GITHUB_TOKEN auth and strips the
    ``github-models/`` prefix from the model string, mutating ``kwargs`` in
    place. Caller then instantiates the openai-compatible provider class.
    """
    kwargs.setdefault("base_url", "https://models.github.ai/inference")
    kwargs.setdefault(
        "api_key",
        os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", ""),
    )
    raw_model = kwargs.get("model", "openai/gpt-4.1")
    if raw_model.startswith("github-models/"):
        kwargs["model"] = raw_model[len("github-models/") :]


def _apply_ollama_cloud_defaults(kwargs: dict[str, Any]) -> None:
    """Pre-configure Ollama Cloud routing onto the openai-compatible backend.

    Injects the ``https://ollama.com`` endpoint + ``OLLAMA_API_KEY`` auth and
    strips a leading ``ollama-cloud:`` prefix from the model string, mutating
    ``kwargs`` in place. Caller then instantiates the openai-compatible
    provider class, which appends ``/v1/chat/completions`` itself — so the
    base URL is stored WITHOUT the ``/v1`` suffix (``OLLAMA_CLOUD_BASE_URL``
    may include or omit it; we normalise).
    """
    base_url = os.environ.get("OLLAMA_CLOUD_BASE_URL", "https://ollama.com").strip()
    # The provider appends /v1/chat/completions, so strip a trailing /v1.
    if base_url.endswith("/v1"):
        base_url = base_url[: -len("/v1")]
    kwargs.setdefault("base_url", base_url.rstrip("/"))
    kwargs.setdefault("api_key", os.environ.get("OLLAMA_API_KEY", ""))
    raw_model = kwargs.get("model", "")
    if raw_model.startswith("ollama-cloud:"):
        kwargs["model"] = raw_model[len("ollama-cloud:") :]


def _resolve_canonical(provider_name: str) -> str:
    """Resolve a provider name or alias to its canonical name."""
    normalised = provider_name.strip().lower()
    canonical = _PROVIDER_ALIASES.get(normalised)
    if canonical is None:
        known = sorted(_PROVIDER_ALIASES.keys())
        raise ValueError(f"Unknown LLM provider: {provider_name!r}. Supported values: {known}")
    return canonical


def _instantiate(module_path: str, class_name: str, **kwargs: Any) -> BaseLLMProvider:
    """Lazy-import a provider class and instantiate it."""
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(f"Failed to import provider module '{module_path}': {exc}") from exc

    provider_cls = getattr(module, class_name)
    return provider_cls(**kwargs)


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def get_provider(provider_name: str, phase: str, **kwargs: Any) -> BaseLLMProvider:
    """Get the agentic provider for the given name.

    All execution phases use agentic providers (file operations + code
    execution). ``phase`` is accepted for API stability and logging.

    Args:
        provider_name: Case-insensitive provider identifier (e.g. "codex",
            "gemini", "claude", "ollama").
        phase: Execution phase ("spec", "planning", "coding", "qa",
            "qa_fixer").
        **kwargs: Forwarded to the provider constructor.

    Returns:
        A ``BaseLLMProvider`` instance (not yet entered).

    Raises:
        ValueError: If provider_name is unrecognised, or the provider has no
            agentic implementation.
    """
    canonical = _resolve_canonical(provider_name)

    # GitHub Models routes through the openai-compatible backend with
    # GitHub-specific defaults pre-injected (epic #87 / #88).
    if canonical == "github-models":
        _apply_github_models_defaults(kwargs)
    # Ollama Cloud likewise routes through openai-compatible with its hosted
    # endpoint + OLLAMA_API_KEY pre-injected (issue #94).
    elif canonical == "ollama-cloud":
        _apply_ollama_cloud_defaults(kwargs)

    if canonical not in _AGENTIC_REGISTRY:
        raise ValueError(
            f"Provider '{provider_name}' does not support agentic mode "
            f"needed for '{phase}' phase. Supported agentic providers: "
            f"{sorted(_AGENTIC_REGISTRY.keys())}"
        )

    module_path, class_name = _AGENTIC_REGISTRY[canonical]

    logger.debug(
        "get_provider: phase=%r canonical=%r class=%s kwargs_keys=%s",
        phase,
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
    return sorted(_AGENTIC_REGISTRY.keys())


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
            logger.debug("get_tool_fallback_provider: %s failed: %s", provider_name, exc)
            continue

    logger.warning("get_tool_fallback_provider: no fallback available")
    return None


__all__ = [
    "get_provider",
    "get_tool_fallback_provider",
    "list_provider_aliases",
    "list_providers",
]
