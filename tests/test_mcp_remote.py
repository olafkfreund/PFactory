"""Tests for the Remote HTTP+SSE MCP server (Epic #50 / Issue #83).

Three concerns:

1. The feature flag (``is_enabled()``) is off by default and reads
   ``PFACTORY_MCP_REMOTE_ENABLED`` correctly.
2. The auth adapter rejects missing / malformed / unknown keys with
   single-line operator-actionable messages, and ``require_scope`` is
   strict.
3. ``dispatch_tool_call`` enforces the right scope per tool, returns
   the MCP ``content[]`` envelope, and surfaces HTTP errors as
   ``isError`` content blocks (not exceptions).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add the web-server module to sys.path the same way the other tests do.
_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))


from server.mcp_remote import is_enabled
from server.mcp_remote.auth import (
    MCP_READ_SCOPE,
    MCP_WRITE_SCOPE,
    AuthenticatedKey,
    MCPAuthError,
    _hash_key,
    _strip_bearer,
    require_scope,
)
from server.mcp_remote.tools import (
    dispatch_tool_call,
    get_tool_definitions,
)

# ── Feature flag ────────────────────────────────────────────────────


def test_is_enabled_default_off(monkeypatch):
    monkeypatch.delenv("PFACTORY_MCP_REMOTE_ENABLED", raising=False)
    assert is_enabled() is False


@pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes", "YES", "on"])
def test_is_enabled_truthy_values(val, monkeypatch):
    monkeypatch.setenv("PFACTORY_MCP_REMOTE_ENABLED", val)
    assert is_enabled() is True


@pytest.mark.parametrize("val", ["false", "0", "no", "", "off", "maybe"])
def test_is_enabled_falsy_values(val, monkeypatch):
    monkeypatch.setenv("PFACTORY_MCP_REMOTE_ENABLED", val)
    assert is_enabled() is False


# ── Catalog shape ──────────────────────────────────────────────────


def test_twelve_tools_in_full_catalog():
    """V1 (8) + V1.1 (4) = the full 12-tool catalog from issue #83."""
    defs = get_tool_definitions()
    names = {d["name"] for d in defs}
    expected = {
        # V1
        "pfactory.list_projects",
        "pfactory.list_tasks",
        "pfactory.get_task",
        "pfactory.get_worktree_diff",
        "pfactory.start_task",
        "pfactory.stop_task",
        "pfactory.approve_plan",
        "pfactory.merge_pr",
        # V1.1
        "pfactory.get_qa_report",
        "pfactory.tail_agent_console",
        "pfactory.reject_plan",
        "pfactory.recover_task",
    }
    assert expected == names


def test_each_tool_has_input_schema():
    defs = get_tool_definitions()
    for d in defs:
        assert "name" in d
        assert "description" in d
        assert "inputSchema" in d
        assert d["inputSchema"].get("type") == "object"


# ── Auth helpers ───────────────────────────────────────────────────


def test_strip_bearer_returns_token():
    assert _strip_bearer("Bearer abc123") == "abc123"


def test_strip_bearer_rejects_non_bearer_scheme():
    assert _strip_bearer("Basic abc123") is None


def test_strip_bearer_handles_missing_header():
    assert _strip_bearer(None) is None
    assert _strip_bearer("") is None


def test_hash_key_deterministic():
    """Hash must match routes/api_keys.py::_hash_key exactly so the DB
    lookup finds the right row."""
    digest = _hash_key("acw_abc123")
    assert len(digest) == 64
    # SHA-256 hex digest of the literal string
    import hashlib
    expected = hashlib.sha256(b"acw_abc123").hexdigest()
    assert digest == expected


def test_require_scope_pass():
    key = AuthenticatedKey(
        key_id="k1",
        scopes=frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}),
        user_id="u1",
    )
    # Should not raise
    require_scope(key, MCP_READ_SCOPE)
    require_scope(key, MCP_WRITE_SCOPE)


def test_require_scope_fail():
    key = AuthenticatedKey(
        key_id="k1", scopes=frozenset({MCP_READ_SCOPE}), user_id=None
    )
    with pytest.raises(MCPAuthError) as exc_info:
        require_scope(key, MCP_WRITE_SCOPE)
    assert "mcp:write" in str(exc_info.value)
    assert "Mint a new key" in str(exc_info.value)


def test_authenticated_key_has_scope():
    key = AuthenticatedKey(
        key_id="k1", scopes=frozenset({"mcp:read"}), user_id=None
    )
    assert key.has_scope("mcp:read")
    assert not key.has_scope("mcp:write")


# ── Dispatch — scope enforcement + envelope shape ─────────────────


@pytest.fixture
def read_only_key():
    return AuthenticatedKey(
        key_id="key-r", scopes=frozenset({MCP_READ_SCOPE}), user_id="u1"
    )


@pytest.fixture
def write_key():
    return AuthenticatedKey(
        key_id="key-w",
        scopes=frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}),
        user_id="u1",
    )


@pytest.fixture
def no_scope_key():
    return AuthenticatedKey(key_id="key-0", scopes=frozenset(), user_id="u1")


async def test_unknown_tool_returns_error(read_only_key):
    result = await dispatch_tool_call("pfactory.imaginary", {}, read_only_key)
    assert result.get("isError") is True
    assert "unknown tool" in result["content"][0]["text"]


async def test_read_tool_blocked_without_read_scope(no_scope_key):
    result = await dispatch_tool_call(
        "pfactory.list_projects", {}, no_scope_key
    )
    assert result.get("isError") is True
    assert "mcp:read" in result["content"][0]["text"]


async def test_write_tool_blocked_with_only_read_scope(read_only_key):
    result = await dispatch_tool_call(
        "pfactory.start_task", {"task_id": "t1"}, read_only_key
    )
    assert result.get("isError") is True
    assert "mcp:write" in result["content"][0]["text"]


@pytest.mark.parametrize(
    "tool_name, args, expected_method, expected_path",
    [
        (
            "pfactory.list_projects",
            {},
            "GET",
            "/api/projects",
        ),
        (
            "pfactory.list_tasks",
            {"project_id": "p1"},
            "GET",
            "/api/projects/p1/tasks",
        ),
        (
            "pfactory.get_task",
            {"task_id": "t1"},
            "GET",
            "/api/tasks/t1",
        ),
        (
            "pfactory.get_worktree_diff",
            {"task_id": "t1"},
            "GET",
            "/api/tasks/t1/worktree/diff",
        ),
    ],
)
async def test_read_tools_hit_correct_endpoint(
    tool_name, args, expected_method, expected_path, read_only_key
):
    mock_call = AsyncMock(return_value={"ok": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(tool_name, args, read_only_key)

    assert mock_call.called
    call_args = mock_call.call_args
    assert call_args.args[0] == expected_method
    assert call_args.args[1] == expected_path
    # No isError
    assert not result.get("isError")
    # Content is JSON-text envelope
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"ok": True}


@pytest.mark.parametrize(
    "tool_name, args, expected_method, expected_path",
    [
        (
            "pfactory.start_task",
            {"task_id": "t1"},
            "POST",
            "/api/tasks/t1/start",
        ),
        (
            "pfactory.stop_task",
            {"task_id": "t1"},
            "POST",
            "/api/tasks/t1/stop",
        ),
        (
            "pfactory.approve_plan",
            {"task_id": "t1"},
            "POST",
            "/api/tasks/t1/approve-plan",
        ),
        (
            "pfactory.merge_pr",
            {"task_id": "t1", "merge_method": "squash"},
            "POST",
            "/api/tasks/t1/worktree/merge",
        ),
    ],
)
async def test_write_tools_hit_correct_endpoint(
    tool_name, args, expected_method, expected_path, write_key
):
    mock_call = AsyncMock(return_value={"ok": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(tool_name, args, write_key)

    assert mock_call.called
    call_args = mock_call.call_args
    assert call_args.args[0] == expected_method
    assert call_args.args[1] == expected_path
    assert not result.get("isError")


async def test_merge_pr_passes_merge_method(write_key):
    """merge_method should land in the JSON body, not the URL."""
    mock_call = AsyncMock(return_value={"ok": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        await dispatch_tool_call(
            "pfactory.merge_pr",
            {"task_id": "t1", "merge_method": "squash"},
            write_key,
        )

    call_args = mock_call.call_args
    assert call_args.kwargs["json"] == {"merge_method": "squash"}


async def test_missing_required_argument_is_actionable(write_key):
    """A KeyError on a missing arg becomes a clear content-block error."""
    mock_call = AsyncMock(return_value={"ok": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(
            "pfactory.get_task", {}, write_key  # missing task_id
        )
    assert result.get("isError") is True
    assert "missing required argument" in result["content"][0]["text"]


async def test_http_error_becomes_isError_content(read_only_key):
    """5xx from the loopback shouldn't crash — surface as content."""
    import httpx

    response = httpx.Response(503, text="upstream broken")
    request = httpx.Request("GET", "http://test/api/projects")
    exc = httpx.HTTPStatusError("", request=request, response=response)
    mock_call = AsyncMock(side_effect=exc)
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(
            "pfactory.list_projects", {}, read_only_key
        )
    assert result.get("isError") is True
    assert "503" in result["content"][0]["text"]


async def test_arbitrary_exception_becomes_isError(read_only_key):
    """An unexpected internal failure shouldn't leak to the MCP client."""
    mock_call = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(
            "pfactory.list_projects", {}, read_only_key
        )
    assert result.get("isError") is True
    assert "boom" in result["content"][0]["text"]


# ════════════════════════════════════════════════════════════════════
# V1.1 tools — qa_report, tail_agent_console, reject_plan, recover_task
# ════════════════════════════════════════════════════════════════════


async def test_get_qa_report_hits_correct_endpoint(read_only_key):
    mock_call = AsyncMock(
        return_value={"content": "# QA report\nAll good", "exists": True}
    )
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(
            "pfactory.get_qa_report", {"task_id": "p1:001"}, read_only_key
        )
    call_args = mock_call.call_args
    assert call_args.args[0] == "GET"
    assert call_args.args[1] == "/api/tasks/p1:001/qa-report"
    assert not result.get("isError")


async def test_qa_report_requires_read_scope(no_scope_key):
    result = await dispatch_tool_call(
        "pfactory.get_qa_report", {"task_id": "p1:001"}, no_scope_key
    )
    assert result.get("isError") is True
    assert "mcp:read" in result["content"][0]["text"]


async def test_tail_agent_console_returns_sse_url(read_only_key, monkeypatch):
    """SSE-in-MCP is awkward; we return the URL for the client to follow."""
    monkeypatch.setenv("PFACTORY_MCP_LOOPBACK_URL", "https://pfactory.example.com")
    # No _call_internal mock needed — this tool builds a URL, not a REST call.
    result = await dispatch_tool_call(
        "pfactory.tail_agent_console", {"task_id": "p1:001"}, read_only_key
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["sse_url"] == (
        "https://pfactory.example.com/api/tasks/p1:001/agent-console/sse"
    )
    assert "auth_hint" in payload


async def test_tail_agent_console_requires_read_scope(no_scope_key):
    result = await dispatch_tool_call(
        "pfactory.tail_agent_console", {"task_id": "p1:001"}, no_scope_key
    )
    assert result.get("isError") is True
    assert "mcp:read" in result["content"][0]["text"]


async def test_reject_plan_hits_correct_endpoint_with_feedback(write_key):
    mock_call = AsyncMock(return_value={"success": True, "feedback_recorded": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(
            "pfactory.reject_plan",
            {"task_id": "p1:001", "feedback": "Plan is too vague"},
            write_key,
        )
    call_args = mock_call.call_args
    assert call_args.args[0] == "POST"
    assert call_args.args[1] == "/api/tasks/p1:001/reject-plan"
    assert call_args.kwargs["json"] == {"feedback": "Plan is too vague"}
    assert not result.get("isError")


async def test_reject_plan_without_feedback(write_key):
    """Feedback is optional — omitting it produces an empty JSON body."""
    mock_call = AsyncMock(return_value={"success": True, "feedback_recorded": False})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        await dispatch_tool_call(
            "pfactory.reject_plan", {"task_id": "p1:001"}, write_key
        )
    call_args = mock_call.call_args
    assert call_args.kwargs["json"] == {}


async def test_reject_plan_blocked_with_only_read_scope(read_only_key):
    result = await dispatch_tool_call(
        "pfactory.reject_plan", {"task_id": "p1:001"}, read_only_key
    )
    assert result.get("isError") is True
    assert "mcp:write" in result["content"][0]["text"]


async def test_recover_task_hits_correct_endpoint(write_key):
    mock_call = AsyncMock(return_value={"recovered": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        result = await dispatch_tool_call(
            "pfactory.recover_task",
            {"task_id": "p1:001", "auto_restart": True},
            write_key,
        )
    call_args = mock_call.call_args
    assert call_args.args[0] == "POST"
    assert call_args.args[1] == "/api/tasks/p1:001/recover"
    # The REST endpoint expects camelCase ``autoRestart`` (see
    # routes/execution.py::RecoverTaskRequest); the MCP tool exposes it
    # as snake_case ``auto_restart`` because that's the Python idiom MCP
    # clients are easier to write against.
    assert call_args.kwargs["json"] == {"autoRestart": True}
    assert not result.get("isError")


async def test_recover_task_default_auto_restart_is_false(write_key):
    mock_call = AsyncMock(return_value={"recovered": True})
    with patch("server.mcp_remote.tools._call_internal", mock_call):
        await dispatch_tool_call(
            "pfactory.recover_task", {"task_id": "p1:001"}, write_key
        )
    call_args = mock_call.call_args
    assert call_args.kwargs["json"] == {"autoRestart": False}


async def test_recover_task_blocked_with_only_read_scope(read_only_key):
    result = await dispatch_tool_call(
        "pfactory.recover_task", {"task_id": "p1:001"}, read_only_key
    )
    assert result.get("isError") is True
    assert "mcp:write" in result["content"][0]["text"]
