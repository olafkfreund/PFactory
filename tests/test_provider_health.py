"""Tests for the provider-credential config pre-flight (RFC-0008 §3.4, #109)."""

from __future__ import annotations

import sys
from pathlib import Path

_WS = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

from server.provider_health import provider_credential_health  # noqa: E402


def test_configured_when_claude_token_present() -> None:
    h = provider_credential_health(env={"CLAUDE_CODE_OAUTH_TOKEN": "tok"})
    assert h["any_configured"] is True
    anthropic = next(p for p in h["providers"] if p["name"] == "anthropic")
    assert anthropic["configured"] is True


def test_none_configured_is_flagged() -> None:
    h = provider_credential_health(env={})
    assert h["any_configured"] is False
    assert all(p["configured"] is False for p in h["providers"])


def test_blank_credential_is_not_configured() -> None:
    # the demo gap: a present-but-empty/whitespace token is NOT configured
    h = provider_credential_health(env={"ANTHROPIC_API_KEY": "   "})
    assert h["any_configured"] is False


def test_multiple_providers_detected() -> None:
    h = provider_credential_health(env={"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"})
    configured = {p["name"] for p in h["providers"] if p["configured"]}
    assert configured == {"gemini", "openai"}
