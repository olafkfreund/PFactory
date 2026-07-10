"""Ollama Cloud provider wiring (issue #94).

Verifies that ``ollama-cloud:<model>`` strings and ``:cloud``-suffixed model
names resolve to the ollama-cloud provider, that the provider factory rewrites
them onto the openai-compatible backend with the https://ollama.com endpoint +
OLLAMA_API_KEY auth, and that none of this shadows the existing
Claude/Codex/local-Ollama routing.

Backend-only — no network, no real provider is entered.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

import phase_config as pc  # noqa: E402
from providers import factory  # noqa: E402

# ---------------------------------------------------------------------------
# Provider inference
# ---------------------------------------------------------------------------


def test_ollama_cloud_prefix_infers_ollama_cloud_provider():
    assert pc.infer_provider_from_model("ollama-cloud:gpt-oss:120b") == "ollama-cloud"
    assert pc.infer_provider_from_model("ollama-cloud:qwen3-coder:480b") == "ollama-cloud"


def test_cloud_suffix_infers_ollama_cloud_provider():
    assert pc.infer_provider_from_model("qwen3-coder:480b-cloud") == "ollama-cloud"
    assert pc.infer_provider_from_model("glm-5:cloud") == "ollama-cloud"


def test_ollama_cloud_does_not_shadow_codex_local_ollama_or_claude():
    # "ollama-cloud:gpt-oss:120b" carries "gpt" but the cloud routing must win.
    assert pc.infer_provider_from_model("ollama-cloud:gpt-oss:120b") != "codex"
    # Bare local Ollama is unaffected.
    assert pc.infer_provider_from_model("ollama:qwen3:14b") == "ollama"
    # Existing routing is untouched.
    assert pc.infer_provider_from_model("gpt-5-codex") == "codex"
    assert pc.infer_provider_from_model("sonnet") == "claude"
    assert pc.infer_provider_from_model("gemini-2.5-pro") == "gemini"


def test_strip_provider_prefix_removes_ollama_cloud_prefix():
    # The ollama model tag (the second colon) must survive stripping.
    assert pc.strip_provider_prefix("ollama-cloud:gpt-oss:120b") == "gpt-oss:120b"
    # A :cloud-suffixed name has no prefix to strip — passes through unchanged.
    assert pc.strip_provider_prefix("glm-5:cloud") == "glm-5:cloud"


# ---------------------------------------------------------------------------
# Factory aliasing + defaults injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias", ["ollama-cloud", "ollama_cloud", "ollamacloud", "ollama.com"]
)
def test_aliases_resolve_to_ollama_cloud(alias):
    assert factory._resolve_canonical(alias) == "ollama-cloud"


def test_bare_ollama_is_still_local():
    # Cloud aliases must not swallow the host-local provider.
    assert factory._resolve_canonical("ollama") == "ollama"
    assert factory._resolve_canonical("local") == "ollama"


def test_defaults_injection_rewrites_endpoint_and_strips_prefix(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ok_test_key")
    monkeypatch.delenv("OLLAMA_CLOUD_BASE_URL", raising=False)
    kwargs = {"model": "ollama-cloud:gpt-oss:120b"}
    factory._apply_ollama_cloud_defaults(kwargs)
    # base_url stored WITHOUT /v1 — the openai-compatible layer appends it.
    assert kwargs["base_url"] == "https://ollama.com"
    assert kwargs["api_key"] == "ok_test_key"
    assert kwargs["model"] == "gpt-oss:120b"


def test_defaults_injection_normalises_v1_suffix(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ok")
    monkeypatch.setenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")
    kwargs = {"model": "glm-5:cloud"}
    factory._apply_ollama_cloud_defaults(kwargs)
    # The /v1 the user supplied is stripped so the provider doesn't double it.
    assert kwargs["base_url"] == "https://ollama.com"
    # No ollama-cloud: prefix on a :cloud name — model passes through.
    assert kwargs["model"] == "glm-5:cloud"


def test_defaults_injection_respects_caller_overrides(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ok_env")
    kwargs = {
        "model": "ollama-cloud:gpt-oss:120b",
        "base_url": "https://self-hosted.example",
        "api_key": "explicit",
    }
    factory._apply_ollama_cloud_defaults(kwargs)
    assert kwargs["base_url"] == "https://self-hosted.example"
    assert kwargs["api_key"] == "explicit"


def test_get_provider_routes_ollama_cloud_to_openai_compatible(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ok_test")
    monkeypatch.delenv("OLLAMA_CLOUD_BASE_URL", raising=False)
    captured = {}

    def fake_instantiate(module_path, class_name, **kwargs):
        captured["module_path"] = module_path
        captured["class_name"] = class_name
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(factory, "_instantiate", fake_instantiate)
    factory.get_provider(
        "ollama-cloud",
        phase="planning",
        model="ollama-cloud:gpt-oss:120b",
        working_dir="/tmp",
    )
    assert captured["module_path"] == "providers.openai_compatible_agentic"
    assert captured["kwargs"]["base_url"] == "https://ollama.com"
    assert captured["kwargs"]["model"] == "gpt-oss:120b"
    assert captured["kwargs"]["api_key"] == "ok_test"
