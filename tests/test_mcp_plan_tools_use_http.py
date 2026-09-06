"""The plan MCP tools must reach the configured server, not a private store (#694).

Before this, ``plan_ingest`` + ``plan_process`` over MCP built a plan inside an
in-process ``PlanService`` living in the MCP subprocess. The tool returned a
full, plausible payload — session_id, board_state, a complete review — while the
plan never reached the server: absent from the portal, absent from
``GET /api/plan/sessions``, gone when the subprocess exited.

So the assertion that matters is not "the tool returned something sensible" —
it did that while broken. It is **which path the call took**: an HTTP request to
the configured server, and no touch of ``plan.service.SERVICE``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock

import pytest

if isinstance(sys.modules.get("claude_agent_sdk"), MagicMock):
    sys.modules.pop("claude_agent_sdk", None)
    sys.modules.pop("claude_agent_sdk.types", None)
    sys.modules.pop("agents.tools_pkg.tools.plan_control", None)

try:
    import claude_agent_sdk  # noqa: F401 — availability probe
except ImportError:
    pytest.skip(
        "claude_agent_sdk not installed in this venv — install with "
        "'npm run install:backend'.",
        allow_module_level=True,
    )

from agents.tools_pkg import http_client
from agents.tools_pkg.tools.plan_control import create_plan_tools


@pytest.fixture
def tools_by_name():
    return {t.name: t.handler for t in create_plan_tools()}


@pytest.fixture
def calls(monkeypatch):
    """Record every HTTP call the tools make, and answer with a stub session."""
    recorded: list[tuple[str, str, dict]] = []

    async def fake_request(method: str, path: str, **kwargs):
        recorded.append((method, path, kwargs))
        return {
            "session_id": "001-x",
            "status": "processed",
            "board_state": "human_review",
            "review": {"gates_passed": True, "aggregate_score": 0.9},
        }

    monkeypatch.setattr(http_client, "request", fake_request)
    return recorded


def _run(handler, args):
    return asyncio.run(handler(args))


def _payload(result):
    return json.loads(result["content"][0]["text"])


def test_ingest_text_posts_to_the_server(tools_by_name, calls):
    _run(tools_by_name["plan_ingest"], {"text": "# Plan\n\nAC#1: it works."})
    assert len(calls) == 1
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/api/plan/sessions/ingest-text")
    # The plan TEXT must be in the request body — a request that carries no
    # plan is exactly the failure this guards (see #692 for the same shape).
    assert kwargs["json"]["text"] == "# Plan\n\nAC#1: it works."


def test_ingest_path_uploads_the_file_bytes(tools_by_name, calls, tmp_path):
    doc = tmp_path / "plan.md"
    doc.write_bytes(b"# From disk\n\nAC#1: bytes arrive.")
    _run(tools_by_name["plan_ingest"], {"path": str(doc)})
    method, path, kwargs = calls[0]
    assert (method, path) == ("POST", "/api/plan/sessions/ingest")
    name, data = kwargs["files"]["file"]
    assert name == "plan.md"
    assert data == b"# From disk\n\nAC#1: bytes arrive."


def test_a_missing_path_is_an_error_not_an_upload(tools_by_name, calls, tmp_path):
    result = _run(tools_by_name["plan_ingest"], {"path": str(tmp_path / "nope.md")})
    assert result.get("is_error") is True
    assert calls == []


def test_ingest_without_text_or_path_is_rejected(tools_by_name, calls):
    result = _run(tools_by_name["plan_ingest"], {})
    assert result.get("is_error") is True
    assert calls == []


@pytest.mark.parametrize(
    ("tool_name", "args", "expected"),
    [
        ("plan_process", {"session_id": "001-x"}, ("POST", "/api/plan/sessions/001-x/process")),
        ("plan_get", {"session_id": "001-x"}, ("GET", "/api/plan/sessions/001-x")),
        ("plan_status", {"session_id": "001-x"}, ("GET", "/api/plan/sessions/001-x")),
        ("plan_list", {}, ("GET", "/api/plan/sessions")),
        ("plan_categories", {}, ("GET", "/api/plan/meta/categories")),
        (
            "plan_approve",
            {"session_id": "001-x", "approver": "olaf"},
            ("POST", "/api/plan/sessions/001-x/approve"),
        ),
        (
            "plan_export_audit_pack",
            {"session_id": "001-x"},
            ("GET", "/api/plan/sessions/001-x/audit-pack"),
        ),
    ],
)
def test_every_tool_goes_over_http(tools_by_name, calls, tool_name, args, expected):
    _run(tools_by_name[tool_name], args)
    assert len(calls) == 1
    assert calls[0][:2] == expected


def test_no_tool_touches_the_local_plan_service(tools_by_name, calls, monkeypatch):
    """The private in-process store is the bug — nothing may reach for it.

    Patching the singleton to explode means any tool that still resolves
    ``plan.service.SERVICE`` fails loudly here rather than quietly writing to a
    store the server will never see.
    """
    import plan.service as plan_service

    class Detonate:
        def __getattr__(self, name):
            raise AssertionError(f"a plan tool used the in-process SERVICE.{name} (#694)")

    monkeypatch.setattr(plan_service, "SERVICE", Detonate())

    for name, args in [
        ("plan_ingest", {"text": "# P\n\nAC#1: x."}),
        ("plan_process", {"session_id": "001-x"}),
        ("plan_list", {}),
        ("plan_approve", {"session_id": "001-x", "approver": "olaf"}),
    ]:
        result = _run(tools_by_name[name], args)
        assert result.get("is_error") is not True, f"{name} errored: {result}"


def test_status_is_a_summary_not_the_whole_session(tools_by_name, calls):
    payload = _payload(_run(tools_by_name["plan_status"], {"session_id": "001-x"}))
    assert payload == {
        "session_id": "001-x",
        "status": "processed",
        "board_state": "human_review",
        "gates_passed": True,
        "aggregate_score": 0.9,
    }


def test_a_server_error_surfaces_as_a_tool_error(tools_by_name, monkeypatch):
    """A failed call must not read as success — that was the original defect."""

    async def boom(method, path, **kwargs):
        raise http_client.MCPHTTPError("PFactory web-server not reachable at http://x")

    monkeypatch.setattr(http_client, "request", boom)
    result = _run(tools_by_name["plan_list"], {})
    assert result.get("is_error") is True
    assert "not reachable" in result["content"][0]["text"]
