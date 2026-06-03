"""Inherited tests for AIFactory's 8 task-control MCP tools.

QUARANTINED for PFactory MVP — Task 2 (#3) replaced those tools with the
seven spec-aware MVP tools tested in ``tests/test_pfactory_mcp_tools.py``.
The inherited tools (task_start, task_stop, task_approve_plan,
task_running, task_get, task_get_logs, etc.) no longer exist; this file
is kept only as historical reference until the broader test rewrite in
Tasks 5-8 settles.

Re-enable individual tests once the corresponding PFactory tool surface
stabilises.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

pytest.skip(
    "Quarantined: AIFactory task-control tools removed in Task 2 (#3); "
    "see tests/test_pfactory_mcp_tools.py for the PFactory replacement suite.",
    allow_module_level=True,
)

# conftest.py pre-mocks claude_agent_sdk to support test files that don't
# need the real SDK. These tests DO need the real SDK so the ``@tool``
# decorator produces actual SdkMcpTool dataclasses (not MagicMocks).
# Drop the mock before importing task_control so the import binds to the
# real ``tool`` decorator.
if isinstance(sys.modules.get("claude_agent_sdk"), MagicMock):
    sys.modules.pop("claude_agent_sdk", None)
    sys.modules.pop("claude_agent_sdk.types", None)
    # And drop the module-under-test so it re-imports the real SDK.
    sys.modules.pop("agents.tools_pkg.tools.task_control", None)

import json

import pytest
from agents.tools_pkg import http_client as hc
from agents.tools_pkg.tools.task_control import create_task_control_tools


@pytest.fixture
def tools_by_name():
    """Return ``{name: handler}`` for the 8 task-control tools.

    The Claude Agent SDK's ``@tool`` decorator produces an ``SdkMcpTool``
    dataclass with ``.name``, ``.description``, ``.input_schema`` and
    ``.handler``. Tests invoke ``.handler(args)`` directly.
    """
    tools = create_task_control_tools()
    assert tools, "claude_agent_sdk not available"
    return {t.name: t.handler for t in tools}


def _make_request_stub(monkeypatch, response, captured=None):
    """Patch ``hc.request`` (the symbol the tools import) with a stub.

    ``response`` can be a callable ``(method, path, **kwargs) -> Any``
    or a static return value.
    """
    async def stub(method, path, **kwargs):
        if captured is not None:
            captured.append({"method": method, "path": path, "kwargs": kwargs})
        if callable(response):
            return response(method, path, **kwargs)
        return response

    # Both the import site and the module level need to see the stub.
    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.request", stub
    )


def _content_text(result):
    """Extract the text block from an MCP tool result."""
    assert "content" in result
    assert isinstance(result["content"], list)
    assert result["content"][0]["type"] == "text"
    return result["content"][0]["text"]


# ── Catalog presence ────────────────────────────────────────────────


def test_all_eight_tools_registered():
    tools = create_task_control_tools()
    names = {t.name for t in tools}
    expected = {
        "task_list",
        "task_running",
        "task_get",
        "task_status",
        "task_get_logs",
        "task_start",
        "task_stop",
        "task_approve_plan",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


# ── Read tools ──────────────────────────────────────────────────────


async def test_task_list_calls_correct_endpoint(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(
        monkeypatch,
        [{"id": "t1", "title": "task one", "status": "running", "project_id": "p1"}],
        captured,
    )
    result = await tools_by_name["task_list"]({"status": "running", "limit": 10})

    assert captured[0]["method"] == "GET"
    assert captured[0]["path"] == "/api/tasks"
    assert captured[0]["kwargs"]["params"] == {"status": "running", "limit": 10}

    payload = json.loads(_content_text(result))
    assert payload["count"] == 1
    assert payload["tasks"][0]["id"] == "t1"


async def test_task_list_handles_wrapped_response(tools_by_name, monkeypatch):
    """Server may return ``{tasks: [...]}`` instead of a bare list."""
    _make_request_stub(monkeypatch, {"tasks": [{"id": "t9", "title": "x"}]})
    result = await tools_by_name["task_list"]({})
    payload = json.loads(_content_text(result))
    assert payload["count"] == 1
    assert payload["tasks"][0]["id"] == "t9"


async def test_task_running(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(
        monkeypatch,
        [{"id": "t2", "title": "running task", "phase": "coding"}],
        captured,
    )
    result = await tools_by_name["task_running"]({})
    assert captured[0]["path"] == "/api/tasks/running"
    payload = json.loads(_content_text(result))
    assert payload["count"] == 1
    assert payload["running"][0]["phase"] == "coding"


async def test_task_get_truncates_heavy_fields(tools_by_name, monkeypatch):
    huge_plan = "x" * 5000
    _make_request_stub(
        monkeypatch,
        {
            "id": "t1",
            "test_plan_json": huge_plan,
            "requirements_json": huge_plan,
            "status": "running",
        },
    )
    result = await tools_by_name["task_get"]({"task_id": "t1"})
    payload = json.loads(_content_text(result))
    assert "[truncated]" in payload["test_plan_json"]
    assert "[truncated]" in payload["requirements_json"]
    # Non-heavy field passes through
    assert payload["id"] == "t1"
    assert payload["status"] == "running"


async def test_task_status_endpoint(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(
        monkeypatch,
        {"phase": "planning", "overall_progress": 25, "model_in_use": "sonnet"},
        captured,
    )
    result = await tools_by_name["task_status"]({"task_id": "t3"})
    assert captured[0]["path"] == "/api/tasks/t3/status"
    payload = json.loads(_content_text(result))
    assert payload["phase"] == "planning"


async def test_task_get_logs_caps_at_500(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"logs": []}, captured)
    await tools_by_name["task_get_logs"]({"task_id": "t4", "tail": 10000})
    assert captured[0]["kwargs"]["params"] == {"tail": 500}


async def test_task_get_logs_default(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"logs": []}, captured)
    await tools_by_name["task_get_logs"]({"task_id": "t4"})
    assert captured[0]["kwargs"]["params"] == {"tail": 100}


# ── Write tools ─────────────────────────────────────────────────────


async def test_task_start(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"ok": True}, captured)
    result = await tools_by_name["task_start"]({"task_id": "t5"})
    assert captured[0]["method"] == "POST"
    assert captured[0]["path"] == "/api/tasks/t5/start"
    payload = json.loads(_content_text(result))
    assert payload["started"] is True
    assert payload["task_id"] == "t5"


async def test_task_stop(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"ok": True}, captured)
    result = await tools_by_name["task_stop"]({"task_id": "t6"})
    assert captured[0]["method"] == "POST"
    assert captured[0]["path"] == "/api/tasks/t6/stop"
    payload = json.loads(_content_text(result))
    assert payload["stopped"] is True


async def test_task_approve_plan(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"ok": True}, captured)
    result = await tools_by_name["task_approve_plan"]({"task_id": "t7"})
    assert captured[0]["path"] == "/api/tasks/t7/approve-plan"
    payload = json.loads(_content_text(result))
    assert payload["approved"] is True


# ── Error propagation ──────────────────────────────────────────────


async def test_http_error_becomes_isError_content(tools_by_name, monkeypatch):
    """MCPHTTPError must NOT raise — it should land as a content block."""

    async def raise_it(method, path, **kwargs):
        raise hc.MCPHTTPError("web-server not reachable at http://x — start it")

    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.request", raise_it
    )
    result = await tools_by_name["task_list"]({})
    assert result.get("isError") is True
    assert "not reachable" in _content_text(result)


async def test_write_error_does_not_silently_swallow(tools_by_name, monkeypatch):
    """Errors on writes must surface as visible failures."""

    async def raise_it(method, path, **kwargs):
        raise hc.MCPHTTPError("token rejected at ~/.pfactory/.token")

    monkeypatch.setattr(
        "agents.tools_pkg.tools.task_control.request", raise_it
    )
    result = await tools_by_name["task_start"]({"task_id": "t8"})
    assert result.get("isError") is True
    assert "token rejected" in _content_text(result)


# ════════════════════════════════════════════════════════════════════
# M2 — destructive tools (confirm-gated) + extra read tools
# ════════════════════════════════════════════════════════════════════


def test_all_fifteen_tools_registered_after_m2():
    tools = create_task_control_tools()
    names = {t.name for t in tools}
    expected = {
        # M1
        "task_list",
        "task_running",
        "task_get",
        "task_status",
        "task_get_logs",
        "task_start",
        "task_stop",
        "task_approve_plan",
        # M2
        "task_create_and_run",
        "task_recover",
        "task_create_pr",
        "task_merge_pr",
        "task_get_diff",
        "project_list",
        "agent_status",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


# ── Confirm-gate behavior ────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool_name, args",
    [
        (
            "task_create_and_run",
            {"project_id": "p1", "title": "x", "description": "y"},
        ),
        ("task_recover", {"task_id": "t1"}),
        ("task_create_pr", {"task_id": "t1"}),
        ("task_merge_pr", {"task_id": "t1"}),
    ],
)
async def test_destructive_tool_refuses_without_confirm(
    tools_by_name, monkeypatch, tool_name, args
):
    """All 4 destructive M2 tools must refuse without confirm=true.

    Confirm-gate is structural — no REST call happens at all (so the
    monkeypatched request would NOT be invoked). We assert that AND the
    response shape so future regressions on either side fail loudly.
    """
    called = []

    async def stub(method, path, **kwargs):
        called.append((method, path))
        return {}

    monkeypatch.setattr("agents.tools_pkg.tools.task_control.request", stub)

    result = await tools_by_name[tool_name](args)
    payload = json.loads(_content_text(result))
    assert payload["requires_confirmation"] is True
    assert "to_proceed" in payload
    assert called == [], (
        f"{tool_name} hit the REST endpoint without confirm=true — confirm-gate broken"
    )


async def test_create_and_run_with_confirm_calls_endpoint(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"task_id": "new123"}, captured)
    result = await tools_by_name["task_create_and_run"](
        {
            "project_id": "p1",
            "title": "Add login",
            "description": "Build a login form",
            "confirm": True,
        }
    )
    assert captured[0]["method"] == "POST"
    assert captured[0]["path"] == "/api/tasks/create-and-run"
    payload = json.loads(_content_text(result))
    assert payload["created_and_started"] is True


async def test_recover_with_confirm(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"ok": True}, captured)
    result = await tools_by_name["task_recover"](
        {"task_id": "t1", "auto_restart": True, "confirm": True}
    )
    assert captured[0]["path"] == "/api/tasks/t1/recover"
    assert captured[0]["kwargs"]["json"] == {"auto_restart": True}
    payload = json.loads(_content_text(result))
    assert payload["recovered"] is True


async def test_create_pr_with_confirm(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(
        monkeypatch, {"pr_url": "https://github.com/x/y/pull/1", "pr_number": 1}, captured
    )
    result = await tools_by_name["task_create_pr"](
        {"task_id": "t1", "title": "Add X", "confirm": True}
    )
    assert captured[0]["path"] == "/api/tasks/t1/worktree/create-pr"
    payload = json.loads(_content_text(result))
    assert payload["created"] is True


async def test_merge_pr_with_confirm(tools_by_name, monkeypatch):
    captured: list = []
    _make_request_stub(monkeypatch, {"merged": True, "sha": "abc"}, captured)
    result = await tools_by_name["task_merge_pr"](
        {"task_id": "t1", "merge_method": "squash", "confirm": True}
    )
    assert captured[0]["path"] == "/api/tasks/t1/worktree/merge"
    assert captured[0]["kwargs"]["json"] == {"merge_method": "squash"}
    payload = json.loads(_content_text(result))
    assert payload["merged"] is True


# ── M2 read tools ─────────────────────────────────────────────────


async def test_task_get_diff_truncates_at_max_lines(tools_by_name, monkeypatch):
    big_diff = "\n".join(f"+ line {i}" for i in range(5000))
    _make_request_stub(monkeypatch, {"diff": big_diff})
    result = await tools_by_name["task_get_diff"](
        {"task_id": "t1", "max_lines": 100}
    )
    payload = json.loads(_content_text(result))
    assert payload["truncated"] is True
    assert payload["lines"] == 101  # 100 lines + truncation marker
    assert "[truncated after 100 lines]" in payload["diff"]


async def test_task_get_diff_under_limit_not_truncated(tools_by_name, monkeypatch):
    small_diff = "\n".join(f"+ line {i}" for i in range(10))
    _make_request_stub(monkeypatch, {"diff": small_diff})
    result = await tools_by_name["task_get_diff"]({"task_id": "t1"})
    payload = json.loads(_content_text(result))
    assert payload["truncated"] is False
    assert payload["lines"] == 10


async def test_task_get_diff_handles_raw_string_response(tools_by_name, monkeypatch):
    """Server might return the diff as raw text, not wrapped — handle both."""
    _make_request_stub(monkeypatch, "diff --git a/x b/x\n+ change")
    result = await tools_by_name["task_get_diff"]({"task_id": "t1"})
    payload = json.loads(_content_text(result))
    assert "change" in payload["diff"]


async def test_project_list(tools_by_name, monkeypatch):
    _make_request_stub(
        monkeypatch,
        [
            {"id": "p1", "name": "alpha", "path": "/x", "git_provider": "github"},
            {"id": "p2", "name": "beta", "path": "/y", "gitProvider": "gitlab"},
        ],
    )
    result = await tools_by_name["project_list"]({})
    payload = json.loads(_content_text(result))
    assert payload["count"] == 2
    # Both camelCase (gitProvider) and snake_case (git_provider) normalize
    assert payload["projects"][0]["git_provider"] == "github"
    assert payload["projects"][1]["git_provider"] == "gitlab"


async def test_agent_status_combines_two_endpoints(tools_by_name, monkeypatch):
    """agent_status makes 2 REST calls and merges them into one payload."""
    calls: list = []

    async def stub(method, path, **kwargs):
        calls.append(path)
        if path.endswith("/status"):
            return {
                "phase": "coding",
                "overall_progress": 42,
                "model_in_use": "sonnet-4-6",
                "current_subtask_id": "st-3",
                "current_subtask": "Wire login endpoint",
            }
        # Full task
        return {
            "id": "t1",
            "phaseModels": {"coding": "sonnet-4-6", "planning": "opus-4-7"},
        }

    monkeypatch.setattr("agents.tools_pkg.tools.task_control.request", stub)

    result = await tools_by_name["agent_status"]({"task_id": "t1"})
    payload = json.loads(_content_text(result))
    # Both endpoints hit
    assert "/api/tasks/t1/status" in calls
    assert "/api/tasks/t1" in calls
    # Merged into a single coherent shape
    assert payload["phase"] == "coding"
    assert payload["model"] == "sonnet-4-6"
    assert payload["overall_progress"] == 42
    assert payload["current_subtask_title"] == "Wire login endpoint"
