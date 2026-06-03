"""
Tests for the Issue #7 helpers in phase_config.

Covers:
- thinking_config_for() — chooses between {"type": "adaptive"} (Opus 4.7),
  {"type": "enabled", "budget_tokens": N} (explicit budget or non-Opus-4.7
  with a fixed level), and None (caller falls back to legacy
  max_thinking_tokens path).
- interleaved_thinking_betas_for() — returns the interleaved-thinking beta
  only for Opus 4.7 + {planner, coder}.
"""

import sys
from pathlib import Path

import pytest

# Add backend to path (mirror tests/test_thinking_level_validation.py)
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "backend"))

from phase_config import (
    INTERLEAVED_THINKING_AGENT_TYPES,
    INTERLEAVED_THINKING_BETA,
    MODEL_ID_MAP,
    interleaved_thinking_betas_for,
    thinking_config_for,
)

_OPUS_47 = "claude-opus-4-7"
_OPUS_46 = "claude-opus-4-6"  # MODEL_ID_MAP["opus-1m"]
_SONNET = MODEL_ID_MAP["sonnet"]
_HAIKU = MODEL_ID_MAP["haiku"]


class TestThinkingConfigFor:
    """thinking_config_for() contract."""

    # ---- Opus 4.7 → always adaptive (ignores thinking_level beyond "none") ----

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_opus47_returns_adaptive_for_any_non_none_level(self, level: str) -> None:
        assert thinking_config_for(_OPUS_47, level) == {"type": "adaptive"}

    def test_opus47_adaptive_has_no_budget_tokens(self) -> None:
        cfg = thinking_config_for(_OPUS_47, "high")
        assert cfg is not None
        assert "budget_tokens" not in cfg

    def test_opus47_none_level_returns_none(self) -> None:
        # Even on Opus 4.7, "none" means thinking is disabled — None signals
        # caller to fall back to legacy max_thinking_tokens path.
        assert thinking_config_for(_OPUS_47, "none") is None

    # ---- explicit_budget always wins, including on Opus 4.7 ----

    def test_explicit_budget_overrides_adaptive_on_opus47(self) -> None:
        cfg = thinking_config_for(_OPUS_47, "high", explicit_budget=8192)
        assert cfg == {"type": "enabled", "budget_tokens": 8192}

    def test_explicit_budget_on_haiku(self) -> None:
        cfg = thinking_config_for(_HAIKU, "high", explicit_budget=4096)
        assert cfg == {"type": "enabled", "budget_tokens": 4096}

    def test_explicit_budget_zero_falls_through(self) -> None:
        # 0 / None / negative budgets should not be treated as explicit;
        # caller may pass them as "absent".
        assert thinking_config_for(_HAIKU, "none", explicit_budget=0) is None
        assert thinking_config_for(_OPUS_47, "high", explicit_budget=None) == {
            "type": "adaptive"
        }

    # ---- Non-Opus-4.7 models with no explicit budget → None (legacy path) ----

    @pytest.mark.parametrize("model", [_OPUS_46, _SONNET, _HAIKU])
    @pytest.mark.parametrize("level", ["none", "low", "medium", "high"])
    def test_other_models_return_none_without_explicit_budget(
        self, model: str, level: str
    ) -> None:
        # The legacy path in core/client.py handles these via max_thinking_tokens.
        assert thinking_config_for(model, level) is None

    # ---- Unknown model → falls into the "non-Opus-4.7" bucket ----

    def test_unknown_model_returns_none_without_budget(self) -> None:
        assert thinking_config_for("claude-unknown-model", "high") is None

    def test_unknown_model_with_explicit_budget_returns_enabled(self) -> None:
        cfg = thinking_config_for("claude-unknown-model", "high", explicit_budget=2048)
        assert cfg == {"type": "enabled", "budget_tokens": 2048}

    # ---- Mutation safety ----

    def test_returned_dict_is_a_fresh_object(self) -> None:
        a = thinking_config_for(_OPUS_47, "high")
        b = thinking_config_for(_OPUS_47, "high")
        assert a is not None and b is not None
        assert a is not b
        a["type"] = "mutated"  # type: ignore[index]
        assert b == {"type": "adaptive"}


class TestInterleavedThinkingBetasFor:
    """interleaved_thinking_betas_for() contract."""

    # ---- Opus 4.7 + qualifying agent → returns the beta ----

    def test_opus47_planner_gets_interleaved_beta(self) -> None:
        assert interleaved_thinking_betas_for(_OPUS_47, "planner") == [
            INTERLEAVED_THINKING_BETA
        ]

    def test_opus47_coder_gets_interleaved_beta(self) -> None:
        assert interleaved_thinking_betas_for(_OPUS_47, "coder") == [
            INTERLEAVED_THINKING_BETA
        ]

    # ---- Opus 4.7 + non-qualifying agent → empty ----

    @pytest.mark.parametrize(
        "agent_type",
        [
            "qa_reviewer",
            "qa_fixer",
            "spec_gatherer",
            "spec_writer",
            "spec_critic",
            "spec_researcher",
        ],
    )
    def test_opus47_non_interleaved_agents_get_empty(self, agent_type: str) -> None:
        assert interleaved_thinking_betas_for(_OPUS_47, agent_type) == []

    # ---- Non-Opus-4.7 models never get the beta ----

    @pytest.mark.parametrize("model", [_OPUS_46, _SONNET, _HAIKU])
    @pytest.mark.parametrize("agent_type", ["planner", "coder", "qa_reviewer"])
    def test_other_models_never_get_interleaved_beta(
        self, model: str, agent_type: str
    ) -> None:
        assert interleaved_thinking_betas_for(model, agent_type) == []

    # ---- Edge cases ----

    def test_unknown_agent_type_returns_empty(self) -> None:
        assert interleaved_thinking_betas_for(_OPUS_47, "totally_unknown") == []

    def test_empty_agent_type_returns_empty(self) -> None:
        assert interleaved_thinking_betas_for(_OPUS_47, "") == []

    # ---- Mutation safety ----

    def test_returned_list_is_a_fresh_object(self) -> None:
        a = interleaved_thinking_betas_for(_OPUS_47, "planner")
        b = interleaved_thinking_betas_for(_OPUS_47, "planner")
        assert a is not b
        a.append("extra-beta")
        assert b == [INTERLEAVED_THINKING_BETA]

    def test_empty_result_is_a_fresh_list_too(self) -> None:
        # No-op corner case — each call returns its own list even when empty.
        a = interleaved_thinking_betas_for(_HAIKU, "planner")
        b = interleaved_thinking_betas_for(_HAIKU, "planner")
        assert a == [] and b == []
        assert a is not b


class TestModuleConstants:
    """Constants used by the helpers — guard against accidental changes."""

    def test_interleaved_thinking_beta_value(self) -> None:
        assert INTERLEAVED_THINKING_BETA == "interleaved-thinking-2025-05-14"

    def test_interleaved_thinking_agent_types_is_frozenset(self) -> None:
        assert isinstance(INTERLEAVED_THINKING_AGENT_TYPES, frozenset)
        assert INTERLEAVED_THINKING_AGENT_TYPES == frozenset({"planner", "coder"})
