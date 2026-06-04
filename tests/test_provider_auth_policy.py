"""Tests for the subscription-only provider auth policy.

PFactory's agentic providers (Codex, Gemini/Antigravity) must run on the
operator's subscription, never on metered API keys — unless explicitly opted in
with PFACTORY_ALLOW_API_KEYS=1. Mirrors the Claude no-API-key posture in
core/auth.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from providers.auth_policy import (  # noqa: E402
    api_keys_allowed,
    scrub_api_keys,
    subscription_only,
)

# ── the policy gate ──────────────────────────────────────────────────────────


def test_subscription_only_is_the_default(monkeypatch):
    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)
    assert subscription_only() is True
    assert api_keys_allowed() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_opt_out_allows_api_keys(monkeypatch, value):
    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", value)
    assert api_keys_allowed() is True
    assert subscription_only() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "", "off"])
def test_falsey_opt_out_stays_subscription_only(monkeypatch, value):
    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", value)
    assert subscription_only() is True


# ── scrub behaviour ──────────────────────────────────────────────────────────


def test_codex_openai_key_stripped_by_default(monkeypatch):
    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)
    env = {"OPENAI_API_KEY": "sk-secret", "PATH": "/usr/bin"}
    out = scrub_api_keys(env, "codex")
    assert "OPENAI_API_KEY" not in out
    assert out["PATH"] == "/usr/bin"          # unrelated vars preserved
    assert env["OPENAI_API_KEY"] == "sk-secret"  # input dict not mutated


def test_gemini_google_keys_stripped_by_default(monkeypatch):
    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)
    env = {"GEMINI_API_KEY": "g", "GOOGLE_API_KEY": "g2", "HOME": "/home/x"}
    out = scrub_api_keys(env, "gemini")
    assert "GEMINI_API_KEY" not in out and "GOOGLE_API_KEY" not in out
    assert out["HOME"] == "/home/x"


def test_keys_preserved_when_opted_in(monkeypatch):
    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", "1")
    env = {"OPENAI_API_KEY": "sk-secret"}
    out = scrub_api_keys(env, "codex")
    assert out["OPENAI_API_KEY"] == "sk-secret"


def test_unknown_provider_is_a_noop(monkeypatch):
    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)
    env = {"OPENAI_API_KEY": "sk", "GOOGLE_API_KEY": "g"}
    out = scrub_api_keys(env, "claude")
    assert out == env                          # claude isn't a subprocess provider here


# ── codex provider integration: the leak point is closed ─────────────────────


def test_codex_subprocess_env_drops_key_and_skips_codex_home(monkeypatch):
    pytest.importorskip("providers.codex_agentic")
    from providers.codex_agentic import CodexAgenticProvider

    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")

    env = CodexAgenticProvider._build_subprocess_env()
    # Subscription-only: the key is gone and no api-key CODEX_HOME was provisioned.
    assert "OPENAI_API_KEY" not in env
    assert env.get("CODEX_HOME", "").find(".pfactory") == -1


def test_codex_subprocess_env_provisions_when_opted_in(monkeypatch, tmp_path):
    pytest.importorskip("providers.codex_agentic")
    from providers.codex_agentic import CodexAgenticProvider

    monkeypatch.setenv("PFACTORY_ALLOW_API_KEYS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    env = CodexAgenticProvider._build_subprocess_env()
    # Opted in: the api-key CODEX_HOME path is provisioned (existing behaviour).
    assert env["CODEX_HOME"] == str(tmp_path / ".pfactory" / "codex-home")
    assert (tmp_path / ".pfactory" / "codex-home" / "auth.json").exists()


def test_global_environ_is_never_mutated(monkeypatch):
    import os
    monkeypatch.delenv("PFACTORY_ALLOW_API_KEYS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    scrub_api_keys(dict(os.environ), "codex")
    assert os.environ["OPENAI_API_KEY"] == "sk-secret"   # the source env is intact
