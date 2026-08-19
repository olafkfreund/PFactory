"""CWE-209 on the insights providers (PFactory#557, batch 1).

Every provider ended its ``except`` handler with:

    await broadcast_event("insights:chunk", {..., "error": str(e)})

``broadcast_event`` is not a response body — it is worse. It pushes the payload
over the WebSocket to every client subscribed to that project, so a connection
failure naming an internal host and port, or an ``OSError`` naming a path on
our disk, was fanned out rather than returned to the one caller who caused it.

These assert on the **broadcast payload**, which is the thing that actually
crosses the boundary, and they drive the providers through their real failure
seams: ``httpx.AsyncClient`` for the HTTP providers, and
``asyncio.create_subprocess_exec`` for the CLI ones.

Mutation check: make ``server.error_ref.error_reference`` return ``str(exc)``
and every case below goes red naming the fragment that escaped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from server.services.insights_providers.claude_provider import ClaudeProvider
from server.services.insights_providers.codex_provider import CodexProvider
from server.services.insights_providers.gemini_provider import GeminiProvider
from server.services.insights_providers.ollama_provider import OllamaProvider
from server.services.insights_providers.openai_compat_provider import (
    OpenAICompatProvider,
)

REF = re.compile(r"\b[0-9a-f]{12}\b")

CREDENTIALS_FILE = "/etc/pfactory/credentials.yaml"
INTERNAL_HOST = "ollama.pfactory.svc.cluster.local"
INTERNAL_PORT = "11434"
BOOM = ConnectionRefusedError(
    f"[Errno 111] Connection refused to {INTERNAL_HOST}:{INTERNAL_PORT} "
    f"while reading {CREDENTIALS_FILE}"
)
LEAKS = (
    CREDENTIALS_FILE,
    INTERNAL_HOST,
    INTERNAL_PORT,
    "ConnectionRefusedError",
    "Errno 111",
)

# module path -> provider factory. Named by the module whose broadcast_event
# must be patched, because each provider imported the symbol into its own
# namespace (`from ...websockets.events import broadcast_event`) and patching
# the definition site would not be seen by any of them.
PROVIDERS = {
    "server.services.insights_providers.ollama_provider": OllamaProvider,
    "server.services.insights_providers.claude_provider": ClaudeProvider,
    "server.services.insights_providers.codex_provider": CodexProvider,
    "server.services.insights_providers.gemini_provider": GeminiProvider,
    "server.services.insights_providers.openai_compat_provider": (
        lambda: OpenAICompatProvider("lmstudio")
    ),
}


async def _broadcasts(module: str, factory: Any, tmp_path: Path) -> list[dict[str, Any]]:
    """Drive one provider into its except handler; return what it broadcast."""
    captured: list[dict[str, Any]] = []

    async def _capture(_event: str, payload: dict[str, Any]) -> None:
        captured.append(payload)

    with (
        patch(f"{module}.broadcast_event", _capture),
        # Both outbound seams, so one helper covers HTTP and CLI providers.
        patch("httpx.AsyncClient", side_effect=BOOM),
        patch("asyncio.create_subprocess_exec", side_effect=BOOM),
    ):
        await factory().send_message(tmp_path, "proj-1", "hello", None, None, None)

    return captured


def _errors(payloads: list[dict[str, Any]]) -> list[str]:
    return [str(p.get("error", "")) for p in payloads if p.get("type") == "error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("module", sorted(PROVIDERS))
async def test_a_provider_failure_is_not_broadcast_verbatim(module: str, tmp_path: Path) -> None:
    payloads = await _broadcasts(module, PROVIDERS[module], tmp_path)

    errors = _errors(payloads)
    # Pinned: if the provider stopped reaching its except handler, there would
    # be no error payload at all and every leak assertion below would pass
    # while testing nothing.
    assert errors, f"{module} broadcast no error payload: {payloads!r}"

    for message in errors:
        for fragment in LEAKS:
            assert fragment not in message, (
                f"{module} broadcast {fragment!r} to every subscribed client: {message!r}"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("module", sorted(PROVIDERS))
async def test_the_broadcast_error_carries_a_correlation_id(module: str, tmp_path: Path) -> None:
    """Redaction must leave the user something to quote to support."""
    errors = _errors(await _broadcasts(module, PROVIDERS[module], tmp_path))
    assert errors
    for message in errors:
        assert REF.search(message), f"{module}: no correlation id in {message!r}"
