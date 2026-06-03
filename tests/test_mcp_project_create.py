#!/usr/bin/env python3
"""Tests for the mcp__pfactory__project_create tool.

`project_create` registers an AIFactory project with PFactory so it can be
handed off for test generation. It is a *local* registration: the caller
supplies ``id`` (matches the AIFactory project_id), ``name``, and
``root_path`` (the local checkout where the feature branch lives). The
entry is persisted to ``<workspace_root>/projects.json``. There is no
clone / git_url mode — PFactory always operates on a local checkout (see
the worktree model in CLAUDE.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# conftest.py pre-mocks claude_agent_sdk for tests that don't need the
# real SDK. These tests DO — without the real `@tool` decorator the tools
# list ends up populated with MagicMocks instead of SdkMcpTool dataclasses.
# Drop the mock + the cached task_control module so the import re-binds
# to the real SDK. (Same trick test_mcp_task_control.py uses.)
if isinstance(sys.modules.get("claude_agent_sdk"), MagicMock):
    sys.modules.pop("claude_agent_sdk", None)
    sys.modules.pop("claude_agent_sdk.types", None)
    sys.modules.pop("agents.tools_pkg.tools.task_control", None)


def _get_project_create_tool():
    """Return the SdkMcpTool wrapper for project_create from the registry."""
    from agents.tools_pkg.tools.task_control import create_task_control_tools

    for t in create_task_control_tools():
        if t.name == "project_create":
            return t
    raise AssertionError("project_create not registered in MCP tool list")


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    """Point the projects store at a throwaway workspace root."""
    import agents.tools_pkg.tools.task_control as tc

    monkeypatch.setattr(tc, "_workspace_root", lambda: tmp_path)
    return tmp_path


def test_project_create_is_in_the_registry():
    tool = _get_project_create_tool()
    assert tool.name == "project_create"
    desc = tool.description.lower()
    assert "root_path" in desc and "aifactory" in desc


@pytest.mark.asyncio
async def test_project_create_rejects_missing_all_fields(isolated_workspace):
    """No id / name / root_path → graceful isError, not a KeyError."""
    tool = _get_project_create_tool()
    result = await tool.handler({})
    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "id" in text and "name" in text and "root_path" in text


@pytest.mark.asyncio
async def test_project_create_rejects_missing_root_path(isolated_workspace):
    tool = _get_project_create_tool()
    result = await tool.handler({"id": "proj-1", "name": "My Project"})
    assert result.get("isError") is True
    assert "root_path" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_project_create_registers_new_project(isolated_workspace):
    tool = _get_project_create_tool()
    result = await tool.handler(
        {"id": "proj-1", "name": "My Project", "root_path": "/tmp/checkout"}
    )
    assert result.get("isError") is not True
    entry = json.loads(result["content"][0]["text"])
    assert entry["id"] == "proj-1"
    assert entry["name"] == "My Project"
    assert entry["root_path"].endswith("/tmp/checkout") or entry["root_path"] == "/tmp/checkout"
    assert "created_at" in entry

    # Persisted to <workspace_root>/projects.json
    saved = json.loads((isolated_workspace / "projects.json").read_text())
    assert [p["id"] for p in saved["projects"]] == ["proj-1"]


@pytest.mark.asyncio
async def test_project_create_rejects_duplicate_id(isolated_workspace):
    tool = _get_project_create_tool()
    args = {"id": "proj-1", "name": "My Project", "root_path": "/tmp/checkout"}
    first = await tool.handler(args)
    assert first.get("isError") is not True

    second = await tool.handler(args)
    assert second.get("isError") is True
    assert "already registered" in second["content"][0]["text"]
