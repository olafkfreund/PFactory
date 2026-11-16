"""Tests for the plan-run token-usage accumulator + the additive RFC-0001 v1.1
``usage`` block on the completion event (#60).

The Plan pipeline is deterministic by default, so the block is honestly zero
unless an LLM seam is supplied — these tests cover both paths plus the tolerant
``usage_from_obj`` normalizer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.completion import build_completion_event  # noqa: E402
from plan.service import PlanService  # noqa: E402
from plan.usage import (  # noqa: E402
    PlanUsage,
    estimate_cost_usd,
    usage_from_obj,
)

_PLAN = """# Refund flow
Add a refund flow to the orders web app.
## Acceptance Criteria
- A finance user can issue a refund
- The order status becomes refunded
"""

_EPIC_JSON = json.dumps(
    {
        "epic_title": "Refund flow",
        "epic_body": "Body",
        "summary": "One feature",
        "children": [
            {"title": "Issue a refund", "body": "AC: finance user can refund"},
        ],
    }
)


# ── PlanUsage accumulator ────────────────────────────────────────────────────


def test_plan_usage_defaults_to_zero():
    u = PlanUsage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.total_tokens == 0
    assert u.cost_usd == 0.0
    assert u.model == ""


def test_plan_usage_add_accumulates_and_keeps_first_model():
    u = PlanUsage()
    u.add(PlanUsage(input_tokens=100, output_tokens=20, cost_usd=0.5, model="claude-a"))
    u.add(PlanUsage(input_tokens=50, output_tokens=10, cost_usd=0.25, model="claude-b"))
    assert u.input_tokens == 150
    assert u.output_tokens == 30
    assert u.total_tokens == 180
    assert u.cost_usd == 0.75
    assert u.model == "claude-a"  # first non-empty wins (dominant id)


def test_plan_usage_add_none_is_noop():
    u = PlanUsage(input_tokens=5)
    u.add(None)
    assert u.input_tokens == 5


def test_event_block_shape():
    block = PlanUsage(input_tokens=10, output_tokens=4, cost_usd=0.01, model="m").as_event_block()
    assert block == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cost_usd": 0.01,
        "model": "m",
    }


# ── usage_from_obj normalizer ────────────────────────────────────────────────


def test_usage_from_dict():
    u = usage_from_obj({"input_tokens": 200, "output_tokens": 50, "model": "claude-x"})
    assert u is not None
    assert (u.input_tokens, u.output_tokens, u.model) == (200, 50, "claude-x")


def test_usage_from_nested_dict_with_openai_aliases():
    u = usage_from_obj({"usage": {"prompt_tokens": 30, "completion_tokens": 7}})
    assert u is not None
    assert (u.input_tokens, u.output_tokens) == (30, 7)


def test_usage_from_anthropic_style_object():
    class _Usage:
        input_tokens = 120
        output_tokens = 18

    class _Message:
        usage = _Usage()
        model = "claude-sonnet-4-6"

    u = usage_from_obj(_Message())
    assert u is not None
    assert (u.input_tokens, u.output_tokens, u.model) == (120, 18, "claude-sonnet-4-6")
    # cost derived from the price table for a known model
    assert u.cost_usd == estimate_cost_usd(120, 18, "claude-sonnet-4-6") > 0


def test_usage_from_obj_returns_none_when_no_tokens():
    assert usage_from_obj({"model": "x"}) is None
    assert usage_from_obj(None) is None
    assert usage_from_obj("just a string") is None


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost_usd(1000, 1000, "some-unknown-model") == 0.0


# ── end-to-end: the completion envelope ──────────────────────────────────────


def test_completion_event_carries_zero_usage_on_deterministic_run():
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    session = svc.process(session.session_id)  # no llm → deterministic
    event = build_completion_event(session)
    assert event["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "model": "",
    }


class _UsageLLM:
    """Decomposer fake that also exposes the call's token usage (#60)."""

    def __init__(self, completion: str):
        self._completion = completion
        self.last_usage = None

    def complete(self, prompt: str) -> str:
        self.last_usage = {
            "input_tokens": 321,
            "output_tokens": 88,
            "model": "claude-sonnet-4-6",
        }
        return self._completion


def test_completion_event_reflects_real_usage_when_llm_supplied():
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    session = svc.process(session.session_id, llm=_UsageLLM(_EPIC_JSON))
    event = build_completion_event(session)
    usage = event["usage"]
    assert usage["input_tokens"] == 321
    assert usage["output_tokens"] == 88
    assert usage["total_tokens"] == 409
    assert usage["model"] == "claude-sonnet-4-6"
    assert usage["cost_usd"] > 0  # known model → non-zero cost


def test_token_ceiling_fails_the_run_with_a_typed_halt(monkeypatch):
    """#1008: usage over PFACTORY_RUN_TOKEN_CEILING fails process() with a reason."""
    from agents.run_guard import TokenCeilingError

    monkeypatch.setenv("PFACTORY_RUN_TOKEN_CEILING", "100")
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    sent: list = []
    monkeypatch.setattr(
        "plan.service.emit_usage_snapshot", lambda s: sent.append(build_completion_event(s))
    )
    with pytest.raises(TokenCeilingError):
        svc.process(session.session_id, llm=_UsageLLM(_EPIC_JSON))
    assert sent[0]["halt_reason"].startswith("token_ceiling")  # CFactory is told
    assert sent[0]["usage"]["total_tokens"] == 409
    session = svc.get(session.session_id)
    assert session.status == "failed"
    event = build_completion_event(session)
    assert event["status"] == "failed"
    assert event["halt_reason"].startswith("token_ceiling: run used 409 tokens")

    # An over-budget session never makes another paid call.
    llm = _UsageLLM(_EPIC_JSON)
    with pytest.raises(TokenCeilingError):
        svc.process(session.session_id, llm=llm)
    assert llm.last_usage is None


def test_token_ceiling_zero_disables(monkeypatch):
    monkeypatch.setenv("PFACTORY_RUN_TOKEN_CEILING", "0")
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Refund flow")
    session = svc.process(session.session_id, llm=_UsageLLM(_EPIC_JSON))
    assert session.status == "processed"
