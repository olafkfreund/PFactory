"""BYO-LLM / air-gapped egress classification (#38).

PFactory already runs against any provider the factory in
``providers/`` supports — Claude, Codex, Gemini, Ollama, and any
OpenAI-compatible endpoint (LM Studio, vLLM, LocalAI, OpenRouter,
Together, Groq, …). What was missing is a way to answer the question a
regulated / privacy-conscious buyer actually asks:

    "If I run PFactory with *this* model, does my source code and the
     generated tests leave my network?"

This module classifies the configured model + endpoint into an
:class:`EgressClass` so the portal / CLI can surface an honest
"LOCAL - endpoint is on your network" badge instead of an unverifiable
marketing claim. The decision is driven by the resolved endpoint host:

  - ``localhost`` / loopback / RFC-1918 private / ``.local`` / ``.internal``
    → **LOCAL** (data stays on your machine or LAN)
  - a routable host on Ollama or a non-managed OpenAI-compatible server
    → **SELF_HOSTED** (your own server — you control egress)
  - a managed third-party API (Anthropic / Google / OpenAI / OpenRouter …)
    → **MANAGED_CLOUD** (data leaves to a third party)

Pure + dependency-light; the endpoint is passed in (or resolved from the
same env vars the factory uses) so this never makes a network call.
"""

from __future__ import annotations

import ipaddress
import os
from enum import Enum
from urllib.parse import urlparse

from phase_config import infer_provider_from_model

# Providers that are always a managed third-party API unless their base
# URL is explicitly repointed at a local proxy.
_MANAGED_PROVIDERS = frozenset({"claude", "codex", "gemini"})

# Default endpoints (mirror the provider constructors + phase_config env).
_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434",
    "openai-compatible": "https://api.openai.com",
}

# Env vars the factory reads for each provider's endpoint.
_BASE_URL_ENV = {
    "ollama": "OLLAMA_BASE_URL",
    "openai-compatible": "OPENAI_COMPATIBLE_BASE_URL",
    "claude": "ANTHROPIC_BASE_URL",
}

# Known managed OpenAI-compatible hosts — data leaves to a third party
# even though the provider type is "openai-compatible".
_MANAGED_OPENAI_HOSTS = frozenset(
    {
        "api.openai.com",
        "openrouter.ai",
        "api.together.xyz",
        "api.together.ai",
        "api.groq.com",
        "api.mistral.ai",
        "api.deepseek.com",
        "api.anyscale.com",
        "generativelanguage.googleapis.com",
    }
)


class EgressClass(str, Enum):
    """Where a run's prompt/code data goes for a given model + endpoint."""

    LOCAL = "local"  # localhost / private — no egress
    SELF_HOSTED = "self_hosted"  # your own routable server
    MANAGED_CLOUD = "managed_cloud"  # third-party managed API


def host_is_local(host: str | None) -> bool:
    """True if *host* is loopback / private / link-local / ``.local`` etc.

    Treats bare hostnames ``localhost``, and any ``*.local`` / ``*.internal``
    suffix as local. IP literals are classified via :mod:`ipaddress`
    (loopback, private RFC-1918, link-local, and the unspecified address).
    """
    if not host:
        return False
    h = host.strip().lower().rstrip(".")
    if h in {"localhost", "ip6-localhost"}:
        return True
    if h.endswith((".local", ".internal", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified)


def resolve_base_url(model: str) -> str | None:
    """Resolve the endpoint URL for *model* from env + provider defaults.

    Returns ``None`` for managed providers with no explicit base-url
    override (i.e. the vendor's own managed API).
    """
    provider = infer_provider_from_model(model)
    env_name = _BASE_URL_ENV.get(provider)
    env_val = os.environ.get(env_name, "").strip() if env_name else ""
    if env_val:
        return env_val
    return _DEFAULT_BASE_URLS.get(provider)


def _host_of(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url if "://" in base_url else f"//{base_url}")
    return parsed.hostname


def classify(model: str, base_url: str | None = None) -> EgressClass:
    """Classify the data-egress posture of running *model* at *base_url*.

    When *base_url* is omitted it is resolved from env + provider defaults.
    """
    provider = infer_provider_from_model(model)
    url = base_url if base_url is not None else resolve_base_url(model)
    host = _host_of(url)

    # A managed provider repointed at a local proxy (e.g. LiteLLM on
    # localhost) is genuinely local.
    if provider in _MANAGED_PROVIDERS:
        if host and host_is_local(host):
            return EgressClass.LOCAL
        return EgressClass.MANAGED_CLOUD

    if provider == "ollama":
        return EgressClass.LOCAL if host_is_local(host) else EgressClass.SELF_HOSTED

    if provider == "openai-compatible":
        if host_is_local(host):
            return EgressClass.LOCAL
        if host and host.lower() in _MANAGED_OPENAI_HOSTS:
            return EgressClass.MANAGED_CLOUD
        return EgressClass.SELF_HOSTED

    # Unknown provider → assume managed (conservative for a privacy badge).
    return EgressClass.MANAGED_CLOUD


def keeps_data_local(model: str, base_url: str | None = None) -> bool:
    """True only when the run keeps all prompt/code data on your network."""
    return classify(model, base_url) is EgressClass.LOCAL


# Plain text, no glyphs: these strings are surfaced by the CLI and by
# egress_report(), which feeds the portal badge, so they land in docs assets,
# screenshots and any UI that renders `badge` verbatim — all of which the fleet
# standard says are plain text (#400). A consumer that wants an icon keys off
# the `egress` enum value, which is already in the report payload, rather than
# parsing a glyph out of a display string.
#
# The wording says ENDPOINT, not traffic. classify() never makes a network call
# (see the module docstring); it classifies the configured endpoint. "no data
# egress" reads as a measurement this module is not in a position to make.
_BADGE = {
    EgressClass.LOCAL: "LOCAL - endpoint is on your network",
    EgressClass.SELF_HOSTED: "SELF-HOSTED - endpoint is your own server",
    EgressClass.MANAGED_CLOUD: "MANAGED CLOUD - endpoint is a third-party API",
}


def egress_report(model: str, base_url: str | None = None) -> dict[str, object]:
    """Portal/CLI-friendly summary of the model's data-egress posture."""
    provider = infer_provider_from_model(model)
    url = base_url if base_url is not None else resolve_base_url(model)
    egress = classify(model, url)
    return {
        "model": model,
        "provider": provider,
        "base_url": url,
        "host": _host_of(url),
        "egress": egress.value,
        "keeps_data_local": egress is EgressClass.LOCAL,
        "badge": _BADGE[egress],
    }


if __name__ == "__main__":  # pragma: no cover - operator verification tool
    # Quick check: does running a given model keep data on your network?
    #   python byo_llm.py ollama:qwen3:14b
    #   OPENAI_COMPATIBLE_BASE_URL=http://localhost:8000/v1 python byo_llm.py openai-compatible:llama
    import json as _json
    import sys as _sys

    _model = _sys.argv[1] if len(_sys.argv) > 1 else "claude-sonnet-4-5-20250929"
    _rep = egress_report(_model)
    print(_json.dumps(_rep, indent=2))
    print(f"\n{_rep['badge']}")
    _sys.exit(0 if _rep["keeps_data_local"] else 1)
