#!/usr/bin/env python3
"""
Unit tests for the GitHub Copilot CLI provider: model-prefix routing, factory
wiring, prefix stripping, and the Codex provider-owned-auth helper.
"""

import json
import sys
from pathlib import Path

import pytest

# Make apps/backend importable
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def test_infer_provider_from_copilot_model():
    """`copilot:` prefixed models route to the 'copilot' provider."""
    from phase_config import infer_provider_from_model

    assert infer_provider_from_model("copilot:claude-sonnet-4.5") == "copilot"
    assert infer_provider_from_model("copilot:claude-sonnet-4") == "copilot"
    # Crucially, copilot:gpt-5 must NOT be misrouted to codex.
    assert infer_provider_from_model("copilot:gpt-5") == "copilot"


def test_strip_provider_prefix_copilot():
    from phase_config import strip_provider_prefix

    assert strip_provider_prefix("copilot:claude-sonnet-4.5") == "claude-sonnet-4.5"
    assert strip_provider_prefix("copilot:gpt-5") == "gpt-5"
    assert strip_provider_prefix("copilot:") == ""


def test_provider_factory_alias_resolution_copilot():
    from providers.factory import _resolve_canonical

    assert _resolve_canonical("copilot") == "copilot"
    assert _resolve_canonical("COPILOT") == "copilot"
    assert _resolve_canonical("github-copilot") == "copilot"
    assert _resolve_canonical("gh-copilot") == "copilot"


def test_factory_instantiates_copilot_provider():
    """Factory returns a CopilotAgenticProvider with the prefix stripped."""
    from providers.copilot_agentic import CopilotAgenticProvider
    from providers.factory import get_provider

    prov = get_provider("copilot", phase="planning", model="copilot:claude-sonnet-4.5")
    assert isinstance(prov, CopilotAgenticProvider)
    assert prov._model == "claude-sonnet-4.5"


def test_copilot_provider_strips_prefix_and_defaults():
    from providers.copilot_agentic import CopilotAgenticProvider

    assert CopilotAgenticProvider(model="copilot:gpt-5")._model == "gpt-5"
    # Empty/unspecified model falls back to the default.
    assert CopilotAgenticProvider(model="")._model == "claude-sonnet-4.5"


def test_copilot_strip_trailer():
    """The post-run usage/billing block is trimmed from captured output."""
    from providers.copilot_agentic import CopilotAgenticProvider

    raw = (
        "● I created the file.\n"
        "✓ Create test_plan.json\n"
        "● DONE.\n\n"
        "Total usage est:       1 Premium request\n"
        "Total duration (API):  3.6s\n"
        "Usage by model:\n    claude-sonnet-4.5 ...\n"
    )
    out = CopilotAgenticProvider._strip_trailer(raw)
    assert "DONE." in out
    assert "Premium request" not in out
    assert "Usage by model" not in out


def test_codex_build_env_provisions_codex_home(monkeypatch, tmp_path):
    """With OPENAI_API_KEY set *and* api-key billing opted in, the Codex provider
    writes an api-key auth.json into a PFactory-owned CODEX_HOME so it never
    depends on the global login. (Default is subscription-only — see
    test_provider_auth_policy.py — which strips the key instead.)"""
    from providers.codex_agentic import CodexAgenticProvider

    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", "1")  # opt into metered API billing
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    env = CodexAgenticProvider._build_subprocess_env()

    codex_home = tmp_path / ".pfactory" / "codex-home"
    assert env["CODEX_HOME"] == str(codex_home)
    auth = json.loads((codex_home / "auth.json").read_text())
    assert auth == {"OPENAI_API_KEY": "sk-test-123"}


def test_codex_build_env_falls_back_without_key(monkeypatch):
    """No OPENAI_API_KEY → inherit the environment (global codex login)."""
    from providers.codex_agentic import CodexAgenticProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = CodexAgenticProvider._build_subprocess_env()
    assert "CODEX_HOME" not in env or env.get("CODEX_HOME") != ""
    # No api-key provisioning happened; CODEX_HOME not forced on.
    assert "CODEX_HOME" not in env
