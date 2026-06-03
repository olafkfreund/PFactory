"""
Provider registry — singleton instances and concurrent detection.
"""

import asyncio
import logging
import time

from .base import ProviderInfo, ProviderStrategy
from .claude_provider import ClaudeProvider
from .codex_provider import CodexProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_compat_provider import OpenAICompatProvider

logger = logging.getLogger(__name__)

# Singleton provider instances
_providers: dict[str, ProviderStrategy] = {}


def _init_providers() -> None:
    """Lazily initialize all provider singletons."""
    global _providers
    if _providers:
        return

    _providers = {
        "claude": ClaudeProvider(),
        "codex": CodexProvider(),
        "gemini": GeminiProvider(),
        "ollama": OllamaProvider(),
        "lmstudio": OpenAICompatProvider("lmstudio"),
        "localai": OpenAICompatProvider("localai"),
        "vllm": OpenAICompatProvider("vllm"),
        "jan": OpenAICompatProvider("jan"),
    }


def get_provider(provider_id: str) -> ProviderStrategy:
    """Get a provider instance by ID. Defaults to Claude."""
    _init_providers()
    provider = _providers.get(provider_id)
    if provider is None:
        logger.warning(f"Unknown provider '{provider_id}', falling back to Claude")
        provider = _providers["claude"]
    return provider


async def _timed_detect(
    provider_id: str, provider: ProviderStrategy
) -> tuple[str, float, ProviderInfo | Exception]:
    """Run a single provider's detect() and record elapsed time."""
    start = time.perf_counter()
    try:
        info = await provider.detect()
        elapsed = time.perf_counter() - start
        return provider_id, elapsed, info
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return provider_id, elapsed, exc


async def detect_all_providers() -> list[ProviderInfo]:
    """Run detection for all providers concurrently."""
    _init_providers()

    total_start = time.perf_counter()

    timed_results = await asyncio.gather(
        *[
            _timed_detect(pid, prov)
            for pid, prov in _providers.items()
        ],
    )

    timings: dict[str, str] = {}
    infos: list[ProviderInfo] = []
    for provider_id, elapsed, result in timed_results:
        timings[provider_id] = f"{elapsed:.3f}s"
        if isinstance(result, Exception):
            logger.warning(f"Provider detection failed for {provider_id}: {result}")
            continue
        infos.append(result)

    total_elapsed = time.perf_counter() - total_start
    timing_details = ", ".join(f"{k}={v}" for k, v in timings.items())
    logger.info(
        f"[Registry] Provider detection completed in {total_elapsed:.2f}s "
        f"({timing_details})"
    )

    return infos
