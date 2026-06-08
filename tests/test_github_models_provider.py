"""GitHub Models provider wiring (epic #87 / #88, Component 1).

Verifies that ``github-models/<publisher>/<model>`` strings resolve to the
github-models provider, that the provider factory rewrites them onto the
openai-compatible backend with the models.github.ai endpoint + GITHUB_TOKEN
auth, and that none of this shadows the existing Claude/Codex routing.

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


def test_github_models_prefix_infers_github_models_provider():
    assert pc.infer_provider_from_model("github-models/openai/gpt-4.1") == "github-models"
    assert pc.infer_provider_from_model("github-models/meta/llama-3.3-70b-instruct") == "github-models"


def test_github_models_does_not_shadow_codex_or_claude():
    # The publisher path contains "gpt"/"openai" but the github-models prefix
    # must win — it is NOT Codex.
    assert pc.infer_provider_from_model("github-models/openai/gpt-4.1") != "codex"
    # Existing routing is untouched.
    assert pc.infer_provider_from_model("gpt-5-codex") == "codex"
    assert pc.infer_provider_from_model("sonnet") == "claude"
    assert pc.infer_provider_from_model("gemini-2.5-pro") == "gemini"


def test_resolve_model_id_passes_github_models_through_unchanged():
    # The factory strips the prefix; the resolver must not mangle it.
    s = "github-models/openai/gpt-4.1"
    assert pc.resolve_model_id(s) == s


def test_shorthand_catalog_maps_to_github_models_strings():
    assert pc.GITHUB_MODELS_SHORTHANDS["gpt-4.1"] == "github-models/openai/gpt-4.1"
    # Shorthands intentionally stay OUT of MODEL_ID_MAP (which routes to Claude).
    assert "gpt-4.1" not in pc.MODEL_ID_MAP


# ---------------------------------------------------------------------------
# Factory aliasing + defaults injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", ["github-models", "gh-models", "githubmodels"])
def test_aliases_resolve_to_github_models(alias):
    assert factory._resolve_canonical(alias) == "github-models"


def test_bare_github_is_not_an_alias():
    # Must not shadow the gh/GitHub API integration.
    with pytest.raises(ValueError):
        factory._resolve_canonical("github")


def test_defaults_injection_rewrites_endpoint_and_strips_prefix(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    kwargs = {"model": "github-models/openai/gpt-4.1"}
    factory._apply_github_models_defaults(kwargs)
    assert kwargs["base_url"] == "https://models.github.ai/inference"
    assert kwargs["api_key"] == "ghp_test_token"
    assert kwargs["model"] == "openai/gpt-4.1"


def test_defaults_injection_falls_back_to_gh_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gho_fallback")
    kwargs = {"model": "github-models/openai/o4-mini"}
    factory._apply_github_models_defaults(kwargs)
    assert kwargs["api_key"] == "gho_fallback"


def test_defaults_injection_respects_caller_overrides(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_env")
    kwargs = {
        "model": "github-models/openai/gpt-4.1",
        "base_url": "https://custom.example/inference",
        "api_key": "explicit",
    }
    factory._apply_github_models_defaults(kwargs)
    # setdefault must not clobber explicit caller values.
    assert kwargs["base_url"] == "https://custom.example/inference"
    assert kwargs["api_key"] == "explicit"


def test_get_provider_routes_github_models_to_openai_compatible(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    captured = {}

    def fake_instantiate(module_path, class_name, **kwargs):
        captured["module_path"] = module_path
        captured["class_name"] = class_name
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(factory, "_instantiate", fake_instantiate)
    factory.get_provider("github-models", phase="planning",
                         model="github-models/openai/gpt-4.1", working_dir="/tmp")
    assert captured["module_path"] == "providers.openai_compatible_agentic"
    assert captured["kwargs"]["base_url"] == "https://models.github.ai/inference"
    assert captured["kwargs"]["model"] == "openai/gpt-4.1"
    assert captured["kwargs"]["api_key"] == "ghp_test"
