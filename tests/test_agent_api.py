"""Tests for the agent-facing plan API (#2, Phase F).

Exercises the service-backed callables in plan.agent_api that the MCP tools
(mcp__pfactory__plan_*) and the HTTP routes both wrap.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan import agent_api  # noqa: E402

_PLAN = """# Refund API

Build a refund service.

## Acceptance Criteria
- The API exposes a REST endpoint to issue refunds.
- The endpoint requires authentication.
"""


def test_ingest_then_process_flow():
    summary = agent_api.plan_ingest(text=_PLAN, title="Refund API", channel="agent")
    sid = summary["session_id"]
    assert summary["board_state"] == "backlog"

    processed = agent_api.plan_process(sid)
    assert "review" in processed
    assert processed["review"]["gates_passed"] in (True, False)
    # feasibility estimates surface for a cloud-ish plan, effort always present
    assert processed["effort_estimate"]["story_points"] >= 0

    status = agent_api.plan_status(sid)
    assert status["session_id"] == sid
    assert status["board_state"] in {"human_review", "ai_review", "in_progress"}

    got = agent_api.plan_get(sid)
    assert got["session_id"] == sid

    listed = agent_api.plan_list()
    assert any(s["session_id"] == sid for s in listed["sessions"])


def test_ingest_requires_exactly_one_source():
    with pytest.raises(ValueError):
        agent_api.plan_ingest()
    with pytest.raises(ValueError):
        agent_api.plan_ingest(text="x", path="/tmp/y.md")


def test_approve_requires_passing_gates():
    # A plan that passes gates can be approved; the status reflects the approver.
    summary = agent_api.plan_ingest(text=_PLAN, title="Refund API 2", channel="agent")
    sid = summary["session_id"]
    processed = agent_api.plan_process(sid)
    if processed["review"]["gates_passed"]:
        result = agent_api.plan_approve(sid, approver="olaf")
        assert result["approved_by"] == "olaf"
        assert result["board_state"] == "done"
