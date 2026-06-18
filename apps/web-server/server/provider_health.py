"""Provider-credential pre-flight for the health endpoint (RFC-0008 §3.4, #109).

A truthful, quota-free check that each LLM provider's credential env var is set
and non-empty. It catches a *missing / un-rotated* token — the gap that silently
produced an empty build in the 2026-06-18 taskboard demo (a consumer that never
received the rotated CLAUDE_CODE_OAUTH_TOKEN). It deliberately does NOT validate
the token live (no quota burn, no false confidence): this is a CONFIGURATION
pre-flight, surfaced by CFactory as a provider-auth health tile.
"""

from __future__ import annotations

import os

# Provider -> the env vars that, if any is set+non-empty, mean the credential is
# configured for that provider. Mirrors the factory secret set.
_PROVIDER_ENVS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anthropic", ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")),
    ("gemini", ("GEMINI_API_KEY",)),
    ("openai", ("OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY")),
    ("ollama", ("OLLAMA_API_KEY", "OLLAMA_API_URL", "OLLAMA_BASE_URL")),
)


def provider_credential_health(env: dict[str, str] | None = None) -> dict:
    """Return ``{providers: [{name, configured}], any_configured: bool}``.

    ``env`` is injectable for tests; defaults to ``os.environ``. ``configured``
    is True when at least one of the provider's credential env vars is present
    and non-empty (whitespace-stripped). ``any_configured`` is False only when
    the service has NO usable provider credential at all — the high-confidence
    "this service cannot authenticate to any model" alert.
    """
    source = os.environ if env is None else env
    providers = [
        {
            "name": name,
            "configured": any(bool((source.get(e) or "").strip()) for e in envs),
        }
        for name, envs in _PROVIDER_ENVS
    ]
    return {
        "providers": providers,
        "any_configured": any(p["configured"] for p in providers),
    }
