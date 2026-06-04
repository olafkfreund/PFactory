"""Tests for the seven PFactory MVP task-control MCP tools.

Each tool operates on the filesystem under ``$PFACTORY_WORKSPACE_ROOT``;
tests use a tmp_path fixture as the workspace root. The
``claude_agent_sdk`` real package is required so the ``@tool``
decorator produces actual ``SdkMcpTool`` dataclasses — if the SDK isn't
installed in this venv, the whole module is skipped.

Covers Task 2 sub-task 2.1 (#3):
  - tool listing
  - task_create_and_run happy + unhappy
  - task_status (existing + unknown)
  - task_list (filtering)
  - project_create (happy + duplicate)
  - project_list
  - report_get (md + json + missing + invalid format)
  - task_rerun (lane gate + happy)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# conftest.py pre-mocks claude_agent_sdk for fast offline test collection;
# these tests need the real ``@tool`` decorator. Drop the mock so the
# tools_pkg re-imports against the actual SDK.
if isinstance(sys.modules.get("claude_agent_sdk"), MagicMock):
    sys.modules.pop("claude_agent_sdk", None)
    sys.modules.pop("claude_agent_sdk.types", None)
    sys.modules.pop("agents.tools_pkg.tools.task_control", None)

try:
    import claude_agent_sdk  # noqa: F401 — used only to detect availability
except ImportError:
    pytest.skip(
        "claude_agent_sdk not installed in this venv — install with "
        "'npm run install:backend' to exercise the MCP server tools.",
        allow_module_level=True,
    )

from agents.tools_pkg.tools.task_control import create_task_control_tools  # noqa: E402

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated PFactory workspace root for one test."""
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path / "pfactory"))
    monkeypatch.setenv("PFACTORY_PORTAL_PORT", "3114")
    # Default: auto-fire OFF. Tests that exercise the auto-fire path
    # opt in by setting PFACTORY_AUTO_PLAN=1 explicitly (see commit 2
    # of Task 5). Keeping the default off here means the existing 21
    # MCP-tool tests assert against the deterministic
    # status=pending immediately-after-create state.
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "0")
    # Same for Gen-Functional auto-advance (Task 6, commit 1): tests
    # that exercise the full planner→gen_functional chain set this to
    # "1" explicitly.
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "0")
    # Same for Evaluator auto-advance (Task 7, commit 1): keep the
    # chain off in the MCP-tool tests; chain tests opt in explicitly.
    monkeypatch.setenv("PFACTORY_AUTO_EVALUATE", "0")
    # Snapshotter (Task 3) needs an AIFactory root too. Build a fake one
    # so the default-happy task_create_and_run flow doesn't trip
    # SnapshotError. Individual tests can scaffold per-spec sources via
    # the `aifactory_spec` helper below.
    aifactory_root = tmp_path / "aifactory"
    aifactory_root.mkdir()
    monkeypatch.setenv("PFACTORY_AIFACTORY_ROOT", str(aifactory_root))
    return tmp_path / "pfactory"


@pytest.fixture
def aifactory_root(tmp_path: Path) -> Path:
    """Convenience: the fake AIFactory root set by the workspace fixture."""
    return tmp_path / "aifactory"


def _scaffold_aifactory_spec(root: Path, project_id: str, spec_id: str) -> Path:
    """Build a minimal AIFactory spec dir so the snapshotter has something to copy."""
    spec_dir = root / "workspaces" / project_id / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text(f"# spec {spec_id}\n\nAcceptance criteria.\n")
    (spec_dir / "implementation_plan.json").write_text('{"phases": []}')
    return spec_dir


@pytest.fixture
def tools(workspace: Path) -> dict:
    """Return ``{name: handler}`` for the seven PFactory MVP tools."""
    raw = create_task_control_tools()
    assert raw, "claude_agent_sdk did not produce tools — check imports"
    return {t.name: t.handler for t in raw}


def _content(result: dict) -> str:
    """Pull the text payload out of the MCP content-block envelope."""
    assert "content" in result
    assert isinstance(result["content"], list)
    assert result["content"], "empty content list"
    return result["content"][0]["text"]


def _payload(result: dict) -> dict:
    """Decode the JSON body of a successful tool response."""
    return json.loads(_content(result))


# ── Tool catalog ─────────────────────────────────────────────────────────


def test_seven_mvp_tools_registered(tools: dict) -> None:
    expected = {
        "task_create_and_run",
        "task_status",
        "task_list",
        "project_list",
        "project_create",
        "report_get",
        "task_rerun",
    }
    assert set(tools.keys()) == expected
    assert len(tools) == 7


def test_removed_aifactory_tools_are_absent(tools: dict) -> None:
    """The inherited AIFactory-shaped tools must NOT be exposed."""
    for removed in ("task_start", "task_stop", "task_approve_plan",
                    "task_running", "task_get", "task_get_logs"):
        assert removed not in tools, f"{removed!r} should have been removed"


# ── project_create + project_list ────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_create_happy(tools: dict, workspace: Path) -> None:
    res = await tools["project_create"]({
        "id": "demo",
        "name": "Demo project",
        "root_path": "/tmp/demo",
    })
    body = _payload(res)
    assert body["id"] == "demo"
    assert body["name"] == "Demo project"
    assert body["root_path"] == "/tmp/demo"
    assert "created_at" in body
    assert (workspace / "projects.json").exists()


@pytest.mark.asyncio
async def test_project_create_duplicate_errors(tools: dict) -> None:
    await tools["project_create"]({"id": "demo", "name": "x", "root_path": "/tmp/x"})
    again = await tools["project_create"]({"id": "demo", "name": "y", "root_path": "/tmp/y"})
    assert again.get("isError") is True
    assert "already registered" in _content(again)


@pytest.mark.asyncio
async def test_project_list_empty(tools: dict) -> None:
    res = await tools["project_list"]({})
    body = _payload(res)
    assert body == {"count": 0, "projects": []}


@pytest.mark.asyncio
async def test_project_list_after_create(tools: dict) -> None:
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["project_create"]({"id": "other", "name": "Other", "root_path": "/tmp/o"})
    res = await tools["project_list"]({})
    body = _payload(res)
    assert body["count"] == 2
    assert {p["id"] for p in body["projects"]} == {"demo", "other"}


# ── task_create_and_run ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_create_and_run_preview(tools: dict, workspace: Path) -> None:
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    res = await tools["task_create_and_run"]({
        "project_id": "demo",
        "spec_id": "001-login",
        "branch": "feature/login",
        "base_ref": "main",
        # confirm omitted -> defaults to False (preview)
    })
    body = _payload(res)
    assert body["preview"] is True
    assert "001-login" in body["would_create"]
    # No workspace created in preview mode
    assert not (workspace / "workspaces" / "demo" / "specs" / "001-login").exists()


@pytest.mark.asyncio
async def test_task_create_and_run_unknown_project(tools: dict) -> None:
    res = await tools["task_create_and_run"]({
        "project_id": "ghost",
        "spec_id": "001",
        "branch": "f/x",
        "base_ref": "main",
        "confirm": True,
    })
    assert res.get("isError") is True
    assert "unknown project_id" in _content(res)


@pytest.mark.asyncio
async def test_task_create_and_run_happy(tools: dict, workspace: Path, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001-login")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    res = await tools["task_create_and_run"]({
        "project_id": "demo",
        "spec_id": "001-login",
        "branch": "feature/login",
        "base_ref": "main",
        "confirm": True,
    })
    body = _payload(res)
    assert body["task_id"] == "001-login"
    assert body["status"] == "pending"
    assert body["portal_url"].endswith("/tasks/001-login")
    spec_dir = workspace / "workspaces" / "demo" / "specs" / "001-login"
    assert spec_dir.is_dir()
    assert (spec_dir / "task.md").exists()
    assert (spec_dir / "status.json").exists()
    for sub in ("context", "tests", "findings", "logs", "memory"):
        assert (spec_dir / sub).is_dir(), f"missing subdir: {sub}"
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "pending"
    # v0.2 lane spine — 'unit' replaced 'functional' per Decision 2
    assert status["lane_progress"]["unit"] == "pending"

    # Snapshotter (Task 3, #4) populated context/
    assert (spec_dir / "context" / "aifactory_spec.md").exists()
    assert (spec_dir / "context" / "aifactory_plan.json").exists()
    assert (spec_dir / "context" / "source.json").exists()
    src = json.loads((spec_dir / "context" / "source.json").read_text())
    assert src["has_spec_md"] is True
    assert src["has_plan_json"] is True
    # branch missing from the (fake) git repo at /tmp/d → diff skipped with warning
    assert src["has_diff_patch"] is False


@pytest.mark.asyncio
async def test_task_create_and_run_missing_aifactory_spec_rolls_back(
    tools: dict, workspace: Path,
) -> None:
    """If the AIFactory spec dir doesn't exist, the partial workspace is unwound."""
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    res = await tools["task_create_and_run"]({
        "project_id": "demo", "spec_id": "ghost-spec",
        "branch": "f/x", "base_ref": "main", "confirm": True,
    })
    assert res.get("isError") is True
    assert "AIFactory spec dir not found" in _content(res)
    # Workspace must NOT linger after a failed snapshot — retry should be possible.
    assert not (workspace / "workspaces" / "demo" / "specs" / "ghost-spec").exists()


@pytest.mark.asyncio
async def test_task_create_and_run_auto_fires_planner(
    tools: dict, workspace: Path, aifactory_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When PFACTORY_AUTO_PLAN=1, task_create_and_run schedules the planner.

    With Task 5 commit 4, the real planner makes an SDK call; here we
    mock the SDK seams so the auto-fire path runs deterministically
    without hitting Anthropic.
    """
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "1")
    _scaffold_aifactory_spec(aifactory_root, "demo", "auto-fire")

    # Mock the planner's SDK seams. Reuses the same shape as
    # tests/test_planner.py's mock_sdk fixture.
    class _FakeAsyncCM:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    async def _resolve(*a, **kw): return _FakeAsyncCM()
    async def _invoke(client, prompt, spec_dir_arg, verbose):
        (spec_dir_arg / "test_plan.json").write_text(json.dumps({
            "feature": "auto-fire-test",
            "workflow_type": "feature",
            "services_involved": [], "phases": [], "final_acceptance": [],
            "status": "in_progress", "planStatus": "pending",
        }))
        return "complete", "mock", {}
    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)

    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    res = await tools["task_create_and_run"]({
        "project_id": "demo", "spec_id": "auto-fire",
        "branch": "f", "base_ref": "main", "confirm": True,
    })
    body = _payload(res)
    assert body["planner_scheduled"] is True

    # Drain the background planner so the workspace reflects post-real-call state.
    from agents.planner import _BG_PLANNER_TASKS
    if _BG_PLANNER_TASKS:
        await asyncio.gather(*list(_BG_PLANNER_TASKS), return_exceptions=True)

    status_path = workspace / "workspaces" / "demo" / "specs" / "auto-fire" / "status.json"
    status = json.loads(status_path.read_text())
    # Empty-plan mock → planned_empty (warning, not failure).
    assert status["status"] == "planned_empty"
    assert (workspace / "workspaces" / "demo" / "specs"
            / "auto-fire" / "test_plan.json").exists()


@pytest.mark.asyncio
async def test_task_create_and_run_does_not_auto_fire_when_disabled(
    tools: dict, workspace: Path, aifactory_root: Path,
) -> None:
    """Default workspace fixture sets PFACTORY_AUTO_PLAN=0 — verify it sticks."""
    _scaffold_aifactory_spec(aifactory_root, "demo", "no-auto")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    res = await tools["task_create_and_run"]({
        "project_id": "demo", "spec_id": "no-auto",
        "branch": "f", "base_ref": "main", "confirm": True,
    })
    body = _payload(res)
    assert body["planner_scheduled"] is False
    status = json.loads((workspace / "workspaces" / "demo" / "specs"
                         / "no-auto" / "status.json").read_text())
    assert status["status"] == "pending"  # no advancement


@pytest.mark.asyncio
async def test_task_create_and_run_duplicate(tools: dict, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    args = {"project_id": "demo", "spec_id": "001", "branch": "f/x",
            "base_ref": "main", "confirm": True}
    await tools["task_create_and_run"](args)
    second = await tools["task_create_and_run"](args)
    assert second.get("isError") is True
    assert "already exists" in _content(second)


# ── task_status ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_status_unknown(tools: dict) -> None:
    res = await tools["task_status"]({"task_id": "ghost-task"})
    assert res.get("isError") is True
    assert "unknown task_id" in _content(res)


@pytest.mark.asyncio
async def test_task_status_existing(tools: dict, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({
        "project_id": "demo", "spec_id": "001",
        "branch": "f/x", "base_ref": "main", "confirm": True,
    })
    res = await tools["task_status"]({"task_id": "001"})
    body = _payload(res)
    assert body["task_id"] == "001"
    assert body["status"] == "pending"
    assert body["phase"] == "created"
    assert "lane_progress" in body


# ── task_list ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_list_empty(tools: dict) -> None:
    res = await tools["task_list"]({})
    body = _payload(res)
    assert body == {"count": 0, "tasks": []}


@pytest.mark.asyncio
async def test_task_list_after_create(tools: dict, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "a")
    _scaffold_aifactory_spec(aifactory_root, "demo", "b")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({
        "project_id": "demo", "spec_id": "a",
        "branch": "f/a", "base_ref": "main", "confirm": True,
    })
    await tools["task_create_and_run"]({
        "project_id": "demo", "spec_id": "b",
        "branch": "f/b", "base_ref": "main", "confirm": True,
    })
    res = await tools["task_list"]({})
    body = _payload(res)
    assert body["count"] == 2
    assert {t["task_id"] for t in body["tasks"]} == {"a", "b"}


@pytest.mark.asyncio
async def test_task_list_filter_by_project(tools: dict, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "p1", "a")
    _scaffold_aifactory_spec(aifactory_root, "p2", "b")
    await tools["project_create"]({"id": "p1", "name": "P1", "root_path": "/tmp/p1"})
    await tools["project_create"]({"id": "p2", "name": "P2", "root_path": "/tmp/p2"})
    await tools["task_create_and_run"]({"project_id": "p1", "spec_id": "a",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    await tools["task_create_and_run"]({"project_id": "p2", "spec_id": "b",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    res = await tools["task_list"]({"project_id": "p2"})
    body = _payload(res)
    assert body["count"] == 1
    assert body["tasks"][0]["task_id"] == "b"


# ── report_get ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_report_get_unknown_task(tools: dict) -> None:
    res = await tools["report_get"]({"task_id": "ghost"})
    assert res.get("isError") is True


@pytest.mark.asyncio
async def test_report_get_invalid_format(tools: dict) -> None:
    res = await tools["report_get"]({"task_id": "any", "format": "csv"})
    assert res.get("isError") is True
    assert "must be 'md' or 'json'" in _content(res)


@pytest.mark.asyncio
async def test_report_get_missing_report(tools: dict, workspace: Path, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    res = await tools["report_get"]({"task_id": "001"})
    assert res.get("isError") is True
    assert "Triager (Task 8) hasn't run" in _content(res)


@pytest.mark.asyncio
async def test_report_get_md_happy(tools: dict, workspace: Path, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    # Simulate Triager (Task 8) having written a report
    report = workspace / "workspaces" / "demo" / "specs" / "001" / "report.md"
    report.write_text("# Report\n\nAll lanes green.\n")
    res = await tools["report_get"]({"task_id": "001", "format": "md"})
    body = _payload(res)
    assert body["format"] == "md"
    assert "All lanes green" in body["body"]


@pytest.mark.asyncio
async def test_report_get_json_happy(tools: dict, workspace: Path, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    report = workspace / "workspaces" / "demo" / "specs" / "001" / "report.json"
    report.write_text('{"lanes": {"functional": "ok"}}')
    res = await tools["report_get"]({"task_id": "001", "format": "json"})
    body = _payload(res)
    assert body["format"] == "json"
    assert "functional" in body["body"]


@pytest.mark.asyncio
async def test_report_get_reads_findings_triage_report(
    tools: dict, workspace: Path, aifactory_root: Path,
) -> None:
    """report_get reads the Triager's real output: findings/triage_report.md (#57)."""
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    # The Triager writes findings/triage_report.{md,json}, NOT report.* at root.
    findings = workspace / "workspaces" / "demo" / "specs" / "001" / "findings"
    findings.mkdir(parents=True, exist_ok=True)
    (findings / "triage_report.md").write_text("# Triage\n\n2 accepted, 1 flagged.\n")
    res = await tools["report_get"]({"task_id": "001", "format": "md"})
    body = _payload(res)
    assert "triage_report.md" in body["path"]
    assert "2 accepted" in body["body"]


@pytest.mark.asyncio
async def test_report_get_findings_precedes_legacy(
    tools: dict, workspace: Path, aifactory_root: Path,
) -> None:
    """When both exist, the findings/triage_report.* wins over legacy report.*."""
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    spec = workspace / "workspaces" / "demo" / "specs" / "001"
    (spec / "report.md").write_text("LEGACY\n")
    (spec / "findings").mkdir(parents=True, exist_ok=True)
    (spec / "findings" / "triage_report.md").write_text("CANONICAL\n")
    body = _payload(await tools["report_get"]({"task_id": "001", "format": "md"}))
    assert "CANONICAL" in body["body"]
    assert "LEGACY" not in body["body"]


# ── task_rerun ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_rerun_disallowed_lane(tools: dict, aifactory_root: Path) -> None:
    """Out-of-scope lanes are rejected with a Decision-2 message.

    'sast'/'dast'/'fuzz' have v0.1 deprecation aliases (map to 'unit'),
    so they're NOT disallowed at this layer — they'd succeed with a
    warning. Use the security lanes without aliases (deps/secrets) to
    test rejection.
    """
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    for lane in ("deps", "secrets", "telepathy"):
        res = await tools["task_rerun"]({"task_id": "001", "lane": lane})
        assert res.get("isError") is True, f"{lane} should be rejected"
        content = _content(res)
        # v0.2 error message references the new lit set
        assert "lit lanes" in content or "not supported" in content


@pytest.mark.asyncio
async def test_task_rerun_unknown_task(tools: dict) -> None:
    res = await tools["task_rerun"]({"task_id": "ghost", "lane": "functional"})
    assert res.get("isError") is True
    assert "unknown task_id" in _content(res)


@pytest.mark.asyncio
async def test_task_rerun_happy_bumps_count(tools: dict, workspace: Path, aifactory_root: Path) -> None:
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    first = await tools["task_rerun"]({"task_id": "001", "lane": "functional"})
    second = await tools["task_rerun"]({"task_id": "001"})  # lane defaults to functional
    assert _payload(first)["rerun_count"] == 1
    assert _payload(second)["rerun_count"] == 2

    status = json.loads((workspace / "workspaces" / "demo" / "specs" / "001"
                         / "status.json").read_text())
    assert status["rerun_count"] == 2
    # v0.2 lane spine — 'unit' replaced 'functional' per Decision 2
    assert status["lane_progress"]["unit"] == "pending"


@pytest.mark.asyncio
async def test_task_rerun_does_not_refire_when_disabled(
    tools: dict, workspace: Path, aifactory_root: Path,
) -> None:
    """Default fixture sets PFACTORY_AUTO_PLAN=0 — rerun records but doesn't fire (#57)."""
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")
    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    body = _payload(await tools["task_rerun"]({"task_id": "001"}))
    assert body["planner_scheduled"] is False
    assert "not auto-fired" in body["note"]


@pytest.mark.asyncio
async def test_task_rerun_refires_planner_when_enabled(
    tools: dict, workspace: Path, aifactory_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With PFACTORY_AUTO_PLAN=1, task_rerun re-fires the Planner (#57).

    Mocks the planner SDK seams (same shape as the create-and-run auto-fire
    test) so the re-fire path runs without hitting a real LLM.
    """
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "1")
    _scaffold_aifactory_spec(aifactory_root, "demo", "001")

    class _FakeAsyncCM:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    async def _resolve(*a, **kw): return _FakeAsyncCM()
    async def _invoke(client, prompt, spec_dir_arg, verbose):
        (spec_dir_arg / "test_plan.json").write_text(json.dumps({
            "feature": "rerun-test", "workflow_type": "feature",
            "services_involved": [], "phases": [], "final_acceptance": [],
            "status": "in_progress", "planStatus": "pending",
        }))
        return "complete", "mock", {}
    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)

    await tools["project_create"]({"id": "demo", "name": "Demo", "root_path": "/tmp/d"})
    # Create with auto-plan on, then drain so we start from a settled state.
    await tools["task_create_and_run"]({"project_id": "demo", "spec_id": "001",
                                        "branch": "f", "base_ref": "main", "confirm": True})
    from agents.planner import _BG_PLANNER_TASKS
    if _BG_PLANNER_TASKS:
        await asyncio.gather(*list(_BG_PLANNER_TASKS), return_exceptions=True)

    body = _payload(await tools["task_rerun"]({"task_id": "001"}))
    assert body["planner_scheduled"] is True
    assert "re-fired" in body["note"]
    if _BG_PLANNER_TASKS:
        await asyncio.gather(*list(_BG_PLANNER_TASKS), return_exceptions=True)
