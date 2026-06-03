#!/usr/bin/env python3
"""
ToolExecutor.extra_roots — agents (e.g. the Ollama provider) must be able to
write into PFactory's per-task spec/workspace dir, which lives outside the SUT
project dir. These tests pin the path-boundary behaviour.
"""

import asyncio
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def test_write_into_extra_root_is_allowed(tmp_path):
    """A path inside an extra root resolves cleanly (no 'Access denied')."""
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    spec = tmp_path / "workspace" / "specs" / "001"
    project.mkdir(parents=True)
    spec.mkdir(parents=True)

    ex = ToolExecutor(working_dir=project, extra_roots=[spec])
    resolved, err = ex._validate_path(str(spec / "test_plan.json"))
    assert err is None
    assert resolved == (spec / "test_plan.json").resolve()


def test_write_into_project_still_allowed(tmp_path):
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    spec = tmp_path / "ws"
    project.mkdir()
    spec.mkdir()

    ex = ToolExecutor(working_dir=project, extra_roots=[spec])
    _resolved, err = ex._validate_path(str(project / "src.py"))
    assert err is None


def test_path_outside_all_roots_denied(tmp_path):
    """A path in neither working_dir nor any extra root is still denied."""
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    spec = tmp_path / "ws"
    outside = tmp_path / "elsewhere"
    for d in (project, spec, outside):
        d.mkdir()

    ex = ToolExecutor(working_dir=project, extra_roots=[spec])
    _resolved, err = ex._validate_path(str(outside / "secret.txt"))
    assert err is not None
    assert "Access denied" in err


def test_no_extra_roots_preserves_strict_boundary(tmp_path):
    """Without extra_roots the executor enforces the single working_dir root."""
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    project.mkdir()
    ex = ToolExecutor(working_dir=project)
    _resolved, err = ex._validate_path(str(tmp_path / "outside.txt"))
    assert err is not None
    assert "Access denied" in err


def test_write_execute_into_extra_root(tmp_path):
    """End-to-end: a Write tool call into the extra root actually writes."""
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    spec = tmp_path / "ws" / "specs" / "001"
    project.mkdir(parents=True)
    spec.mkdir(parents=True)

    ex = ToolExecutor(working_dir=project, extra_roots=[spec])
    target = spec / "test_plan.json"
    result = asyncio.run(
        ex.execute("Write", {"file_path": str(target), "content": '{"phases": []}'})
    )
    assert not result.is_error, result.content
    assert target.read_text() == '{"phases": []}'


# ── Robustness against local-model malformed tool calls (qwen2.5-coder) ──────

def test_non_dict_tool_input_is_recoverable_error(tmp_path):
    """A list (not object) as tool input must NOT crash — it returns an error
    result so the agent session survives and the model can retry."""
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    project.mkdir()
    ex = ToolExecutor(working_dir=project)
    result = asyncio.run(ex.execute("Write", [{"file_path": "x", "content": "y"}]))
    assert result.is_error
    assert "must be a JSON object" in result.content


def test_write_content_as_list_of_lines(tmp_path):
    """content passed as a list of strings is joined into text."""
    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    project.mkdir()
    ex = ToolExecutor(working_dir=project)
    target = project / "out.txt"
    result = asyncio.run(
        ex.execute("Write", {"file_path": str(target), "content": ["a", "b", "c"]})
    )
    assert not result.is_error, result.content
    assert target.read_text() == "a\nb\nc"


def test_write_content_as_json_structure(tmp_path):
    """content passed as a dict/list structure serialises to valid JSON."""
    import json

    from tools.executor import ToolExecutor

    project = tmp_path / "project"
    project.mkdir()
    ex = ToolExecutor(working_dir=project)
    target = project / "test_plan.json"
    payload = {"phases": [{"subtasks": [{"id": "t1"}]}]}
    result = asyncio.run(
        ex.execute("Write", {"file_path": str(target), "content": payload})
    )
    assert not result.is_error, result.content
    assert json.loads(target.read_text()) == payload
