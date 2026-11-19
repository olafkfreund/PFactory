"""New model ids (#1089): shorthands, legacy pins, provider routing, prices, cache floors."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from core.cache import _min_cacheable_tokens
from phase_config import (
    DEFAULT_PHASE_MODELS,
    MODEL_ID_MAP,
    infer_provider_from_model,
    resolve_model_id,
)
from plan.usage import estimate_cost_usd

_CATALOG = Path(__file__).parent.parent / "apps/backend/plan/emit/model-catalog.json"


@pytest.mark.parametrize(
    ("alias", "full"),
    [
        ("opus", "claude-opus-5-5"),
        ("sonnet", "claude-sonnet-5-5"),
        ("haiku", "claude-haiku-5-5"),
        ("fable", "claude-fable-5-2"),
        # legacy pins keep resolving
        ("opus-4.8", "claude-opus-4-8"),
        ("opus-4.7", "claude-opus-4-7"),
        ("sonnet-5", "claude-sonnet-5"),
        ("sonnet-4.6", "claude-sonnet-4-6"),
        ("haiku-4.5", "claude-haiku-4-5-20251001"),
    ],
)
def test_shorthands_and_legacy_aliases(alias, full, monkeypatch):
    for var in (
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    assert resolve_model_id(alias) == full
    assert infer_provider_from_model(alias) == "claude"


def test_default_phases_use_opus_shorthand():
    assert set(DEFAULT_PHASE_MODELS.values()) == {"opus"}
    assert MODEL_ID_MAP["opus"] == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gpt-6.2-sol", "codex"),
        ("gpt-6-luna", "codex"),
        ("gpt-5.6-terra", "codex"),
        ("gemini-4-carbon-medium", "gemini"),
        ("gemini-3.8-flash-high", "gemini"),
        ("claude-fable-5-2", "claude"),
    ],
)
def test_provider_inference_for_new_families(model, provider):
    assert infer_provider_from_model(model) == provider


def test_catalog_has_new_claude_prices():
    models = json.loads(_CATALOG.read_text())["models"]
    price = {
        k: (v["price"]["in_per_mtok"], v["price"]["out_per_mtok"])
        for k, v in models.items()
        if v["provider"] == "claude"
    }
    assert price["claude-fable-5-2"] == (8, 40)
    assert price["claude-opus-5-5"] == (4, 20)
    assert price["claude-sonnet-5-5"] == (2, 10)
    assert price["claude-haiku-5-5"] == (0.1, 0.5)
    assert models["claude-sonnet-5"]["available"] is False


def test_usage_prices_and_cache_floors():
    assert estimate_cost_usd(1_000_000, 1_000_000, "claude-opus-5-5") == 24.0
    assert estimate_cost_usd(1_000_000, 1_000_000, "claude-haiku-5-5") == 0.6
    assert estimate_cost_usd(1_000_000, 1_000_000, "claude-sonnet-5") == 12.0
    assert _min_cacheable_tokens("claude-sonnet-5-5") == 512
    assert _min_cacheable_tokens("claude-sonnet-5") == 1024
    assert _min_cacheable_tokens("claude-haiku-5-5") == 512
