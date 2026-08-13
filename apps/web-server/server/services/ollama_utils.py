"""Shared Ollama helpers.

Extracted so routes/settings.py and routes/git.py share one ``/api/tags`` fetch
and one embedding-keyword set instead of each re-implementing them.
"""

import httpx

from .url_safety import assert_safe_outbound_url

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# Name substrings that mark an Ollama model as embedding-only.
OLLAMA_EMBEDDING_KEYWORDS = {"embed", "minilm", "bge", "gte", "e5"}


async def fetch_ollama_models(base_url: str | None = None) -> list[dict]:
    """GET ``{base_url}/api/tags`` and return the raw ``models`` list.

    Raises on connection/HTTP error — callers decide how to surface it (some
    want an error envelope, others an empty list). ``base_url`` reaches here
    from a request, so it is guarded first and raises ``ValueError`` when it
    points somewhere we refuse to fetch (CodeQL py/partial-ssrf). Permissive
    posture: a self-hosted Ollama is the whole point of the setting.
    """
    tags_url = assert_safe_outbound_url(
        f"{base_url or DEFAULT_OLLAMA_BASE_URL}/api/tags", allow_private=True
    )
    # httpx does not follow redirects unless asked, so no separate hop guard.
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(tags_url)
        response.raise_for_status()
        return response.json().get("models", [])


def is_embedding_model(name: str, keywords: set[str] = OLLAMA_EMBEDDING_KEYWORDS) -> bool:
    """True if ``name`` matches any embedding keyword (case-insensitive)."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in keywords)
