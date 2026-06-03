#!/usr/bin/env python3
"""Tests for the V1 delegation gap fixes (#144).

Three gaps + one bonus:
- Gap 1: wizard-created tasks now go through the delegation runner
- Gap 2: ``_write_spec_dir`` injects ``enableDelegation`` when the project
         setting ``delegateByDefault`` is on
- Gap 3: ``run_delegation`` awaits the planner subprocess before reading
         the plan
- Bonus: a second invocation against the same issue skips the comment
         post and detects the marker.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------------------
# Gap 2 — _write_spec_dir injects metadata.enableDelegation
# ---------------------------------------------------------------------------


def test_write_spec_dir_injects_delegation_when_default_on(tmp_path: Path):
    from server.services.auto_fix_service import _write_spec_dir

    issue = {
        "number": 42,
        "title": "Add /uptime endpoint",
        "body": "Body",
        "state": "open",
        "labels": [],
        "url": "https://example.test/i/42",
    }
    spec_name = _write_spec_dir(
        tmp_path, issue, "github", delegate_by_default=True
    )
    req = json.loads((tmp_path / ".pfactory" / "specs" / spec_name / "requirements.json").read_text())
    assert req["metadata"]["enableDelegation"] is True
    assert req["metadata"]["githubIssueNumber"] == 42


def test_write_spec_dir_omits_delegation_when_default_off(tmp_path: Path):
    from server.services.auto_fix_service import _write_spec_dir

    issue = {
        "number": 7,
        "title": "Add /version",
        "body": "Body",
        "state": "open",
        "labels": [],
        "url": "https://example.test/i/7",
    }
    spec_name = _write_spec_dir(tmp_path, issue, "github")
    req = json.loads((tmp_path / ".pfactory" / "specs" / spec_name / "requirements.json").read_text())
    assert "metadata" not in req


# ---------------------------------------------------------------------------
# Gap 3 — run_delegation awaits the planner subprocess
# ---------------------------------------------------------------------------


def _stub_proc(returncode: int = 0, wait_delay: float = 0.0):
    """A fake asyncio subprocess that resolves wait() cleanly."""
    proc = MagicMock()

    async def _wait():
        if wait_delay:
            await asyncio.sleep(wait_delay)
        return returncode

    proc.wait = _wait
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_run_delegation_awaits_planner_before_reading_plan(tmp_path: Path):
    """The runner must call proc.wait() BEFORE rendering the comment, so
    the planner has had a chance to write test_plan.json."""
    from server.services import delegation_runner

    project_path = tmp_path / "proj"
    spec_dir = project_path / ".pfactory" / "specs" / "001-gh42-test"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Title\n\nBody")

    # The planner writes test_plan.json from inside wait()
    # so we can prove the comment renderer sees a populated plan.
    plan_payload = {
        "acceptance_criteria": ["it works"],
        "phases": [
            {"name": "Impl", "subtasks": [{"description": "do it",
                                            "affected_files": ["src/a.py"]}]}
        ],
    }

    async def _fake_wait():
        # Simulate planner taking a moment, then writing the file.
        await asyncio.sleep(0.01)
        (spec_dir / "test_plan.json").write_text(json.dumps(plan_payload))
        return 0

    proc = MagicMock()
    proc.wait = _fake_wait
    proc.kill = MagicMock()

    mock_agent = MagicMock()
    mock_agent.start_task_execution = AsyncMock(return_value=proc)

    mock_provider = MagicMock()
    mock_provider.repo = "acme/widgets"
    mock_provider.api_get = AsyncMock(return_value=[])  # no existing comments
    mock_provider.add_comment = AsyncMock(return_value=1)
    mock_provider.assign_to_user = AsyncMock(return_value=None)

    with patch(
        "server.services.agent_service.get_agent_service",
        return_value=mock_agent,
    ), patch(
        "server.websockets.events.emit_task_status", new=AsyncMock()
    ), patch(
        "server.websockets.events.broadcast_event", new=AsyncMock()
    ):
        result = await delegation_runner.run_delegation(
            project_id="proj-1",
            project_path=project_path,
            spec_id="001-gh42-test",
            issue_number=42,
            provider=mock_provider,
        )

    # The comment posted MUST include the populated plan (proving
    # run_delegation waited for the planner before rendering).
    posted_body = mock_provider.add_comment.await_args.args[1]
    assert "do it" in posted_body, "Plan content missing — proc.wait() not awaited"
    assert "src/a.py" in posted_body
    assert "_Plan structure unavailable._" not in posted_body
    assert result["status"] == "delegated"
    assert result["commentPosted"] is True
    assert result["copilotAssigned"] is True


@pytest.mark.asyncio
async def test_run_delegation_planner_timeout_does_not_block(tmp_path: Path, monkeypatch):
    """A hung planner is killed and the runner proceeds with degraded comment."""
    from server.services import delegation_runner

    monkeypatch.setattr(delegation_runner, "PLANNER_TIMEOUT_SECONDS", 0.05)

    project_path = tmp_path / "proj"
    spec_dir = project_path / ".pfactory" / "specs" / "001-gh99-hang"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Hung\n\nBody")

    async def _hang_forever():
        await asyncio.sleep(10)

    proc = MagicMock()
    proc.wait = _hang_forever
    proc.kill = MagicMock()

    mock_agent = MagicMock()
    mock_agent.start_task_execution = AsyncMock(return_value=proc)

    mock_provider = MagicMock()
    mock_provider.repo = "acme/widgets"
    mock_provider.api_get = AsyncMock(return_value=[])
    mock_provider.add_comment = AsyncMock(return_value=1)
    mock_provider.assign_to_user = AsyncMock(return_value=None)

    with patch(
        "server.services.agent_service.get_agent_service",
        return_value=mock_agent,
    ), patch(
        "server.websockets.events.emit_task_status", new=AsyncMock()
    ), patch(
        "server.websockets.events.broadcast_event", new=AsyncMock()
    ):
        result = await delegation_runner.run_delegation(
            project_id="proj-1",
            project_path=project_path,
            spec_id="001-gh99-hang",
            issue_number=99,
            provider=mock_provider,
        )

    proc.kill.assert_called_once()
    assert result["status"] == "delegated"
    # Degraded comment is still posted + Copilot still assigned.
    assert result["commentPosted"] is True
    posted_body = mock_provider.add_comment.await_args.args[1]
    assert "_Plan structure unavailable._" in posted_body


# ---------------------------------------------------------------------------
# Bonus — dedupe: skip re-posting when marker comment already exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_delegation_skips_comment_if_marker_already_present(
    tmp_path: Path,
):
    from server.services import delegation_runner
    from server.services.delegation_runner import ENRICHMENT_MARKER

    project_path = tmp_path / "proj"
    spec_dir = project_path / ".pfactory" / "specs" / "001-gh42-dup"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("# Title\n\nBody")
    (spec_dir / "test_plan.json").write_text(
        json.dumps({"acceptance_criteria": ["a"], "subtasks": []})
    )

    proc = _stub_proc()
    mock_agent = MagicMock()
    mock_agent.start_task_execution = AsyncMock(return_value=proc)

    mock_provider = MagicMock()
    mock_provider.repo = "acme/widgets"
    mock_provider.api_get = AsyncMock(
        return_value=[
            {"id": 999, "body": f"{ENRICHMENT_MARKER}\n\n…prior run output…"},
        ]
    )
    mock_provider.add_comment = AsyncMock(return_value=1)
    mock_provider.assign_to_user = AsyncMock(return_value=None)

    with patch(
        "server.services.agent_service.get_agent_service",
        return_value=mock_agent,
    ), patch(
        "server.websockets.events.emit_task_status", new=AsyncMock()
    ), patch(
        "server.websockets.events.broadcast_event", new=AsyncMock()
    ):
        result = await delegation_runner.run_delegation(
            project_id="proj-1",
            project_path=project_path,
            spec_id="001-gh42-dup",
            issue_number=42,
            provider=mock_provider,
        )

    mock_provider.add_comment.assert_not_awaited()
    # Copilot is still (re)assigned — that step is idempotent on GitHub's side.
    mock_provider.assign_to_user.assert_awaited_once()
    assert result["commentPosted"] is False
    assert result["commentSkippedAsDuplicate"] is True
    assert result["copilotAssigned"] is True
