"""End-to-end integration test for the PFactory Planner — Task 5 (#6) commit 6.

Exercises the FULL `run_planner` pipeline against the realistic
``tests/fixtures/planner_smoke/`` workspace (3 ACs, a real diff,
a real target project tree). The SDK is still mocked — but with a
plan response that mirrors what a real LLM would emit for this spec.

This complements the unit tests in `tests/test_planner.py`:
  - Unit tests: each branch of the orchestration in isolation
  - This file: realistic, full-pipeline workspace + multi-AC plan +
    matched verification of every status / file / count expectation

When the real-LLM smoke is run (see `guides/planner-manual-smoke.md`),
the assertions here are the same ones a passing real run should
satisfy.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from agents.planner import _BG_PLANNER_TASKS, run_planner

FIXTURE = Path(__file__).parent / "fixtures" / "planner_smoke"


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_auto_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin PFACTORY_AUTO_GENERATE=0 (Task 6, commit 1) so the planner's
    success path doesn't schedule the stub Gen-Functional during these
    integration assertions."""
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "0")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Mimic what task_create_and_run + the snapshotter produce."""
    spec_dir = tmp_path / "workspaces" / "demo" / "specs" / "042-session-expiry"
    spec_dir.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (spec_dir / sub).mkdir()

    # status.json — as task_create_and_run wrote it
    (spec_dir / "status.json").write_text(json.dumps({
        "task_id": "042-session-expiry",
        "project_id": "demo",
        "spec_id": "042-session-expiry",
        "status": "pending",
        "phase": "created",
    }))

    # context/* — as the snapshotter wrote it (Task 3)
    shutil.copy(FIXTURE / "aifactory_spec.md", spec_dir / "context" / "aifactory_spec.md")
    shutil.copy(FIXTURE / "aifactory_plan.json", spec_dir / "context" / "aifactory_plan.json")
    shutil.copy(FIXTURE / "diff.patch", spec_dir / "context" / "diff.patch")
    (spec_dir / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
        "spec_id": "042-session-expiry",
        "branch": "feature/session-expiry",
        "base_ref": "main",
        "aifactory_spec_dir": "/fake/aifactory/workspaces/demo/specs/042-session-expiry",
        "snapshotted_at": "2026-05-28T00:00:00+00:00",
        "has_spec_md": True,
        "has_plan_json": True,
        "has_diff_patch": True,
        "warnings": [],
    }))

    return spec_dir


@pytest.fixture
def project_dir() -> Path:
    """Pointer at the fixture's project_tree — what the planner Glob/Greps."""
    return FIXTURE / "project_tree"


def _make_realistic_plan(spec_dir_str: str) -> str:
    """Build the plan a competent LLM would emit for the session-expiry spec.

    One phase per AC; concrete pytest verifications; correct targets.
    """
    return json.dumps({
        "feature": "Add session expiry to the auth module",
        "workflow_type": "feature",
        "services_involved": ["backend"],
        "phases": [
            {
                "phase": 1,
                "name": "AC#1: login_user sets expires_at to +24h",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "ac1-login-sets-24h-expiry",
                        "description": "login_user returns a session with expires_at exactly 24h after creation",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1: login_user(...) returns a session whose expires_at is exactly 24 hours after creation",
                        "files_to_create": ["tests/test_login_expiry.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_login_expiry.py",
                            "expected": "exit 0",
                        },
                    },
                    {
                        "id": "ac1-login-preserves-existing-fields",
                        "description": "login_user return shape (id, user_id, email, created_at) unchanged",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1 (regression guard): existing Session shape unchanged",
                        "files_to_create": ["tests/test_login_shape.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_login_shape.py",
                            "expected": "exit 0",
                        },
                    },
                ],
                "parallel_safe": False,
            },
            {
                "phase": 2,
                "name": "AC#2: get_session expires + removes",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "ac2-expired-returns-none",
                        "description": "get_session returns None for an expired session",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::get_session",
                        "rationale": "AC#2: returns None for an expired session",
                        "files_to_create": ["tests/test_get_session_expired.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_get_session_expired.py",
                            "expected": "exit 0",
                        },
                    },
                    {
                        "id": "ac2-expired-removed-from-store",
                        "description": "get_session also removes the expired entry from _STORE",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::get_session",
                        "rationale": "AC#2: removes it from the session store",
                        "files_to_create": ["tests/test_get_session_removes.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_get_session_removes.py",
                            "expected": "exit 0",
                        },
                    },
                ],
                "parallel_safe": False,
            },
            {
                "phase": 3,
                "name": "AC#3: refresh_session honours grace window",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": "ac3-refresh-within-grace",
                        "description": "refresh_session extends expiry by 24h within last 5min",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::refresh_session",
                        "rationale": "AC#3: extends expires_at by another 24h within grace window",
                        "files_to_create": ["tests/test_refresh_within_grace.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_refresh_within_grace.py",
                            "expected": "exit 0",
                        },
                    },
                    {
                        "id": "ac3-refresh-outside-grace-noop",
                        "description": "refresh_session returns unmodified session outside grace window",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::refresh_session",
                        "rationale": "AC#3: outside grace window, returns the unmodified session",
                        "files_to_create": ["tests/test_refresh_outside_grace.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_refresh_outside_grace.py",
                            "expected": "exit 0",
                        },
                    },
                ],
                "parallel_safe": True,
            },
        ],
        "final_acceptance": [
            "All three functions behave per the AC. No regressions in logout_user.",
        ],
        "status": "in_progress",
        "planStatus": "pending",
    })


# ── Mock SDK ────────────────────────────────────────────────────────────


@pytest.fixture
def mock_sdk_realistic(monkeypatch: pytest.MonkeyPatch, workspace: Path):
    """SDK mock that emits the realistic plan for this fixture."""
    class _FakeAsyncCM:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    async def _resolve(*a, **kw): return _FakeAsyncCM()
    async def _invoke(client, prompt, spec_dir_arg, verbose):
        (spec_dir_arg / "test_plan.json").write_text(
            _make_realistic_plan(str(spec_dir_arg))
        )
        return "complete", "mock response", {}

    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)


# ── Integration assertions ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_pipeline_against_realistic_fixture(
    workspace: Path, project_dir: Path, mock_sdk_realistic,
) -> None:
    """Drive the planner against a realistic 3-AC spec and verify
    every shape the downstream Gen-Functional agent depends on."""
    ok = await run_planner(workspace, project_dir, mode="initial")
    assert ok is True

    # 1. status.json transitions to planned with the right metadata
    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["phase"] == "planner_initial_complete"
    assert status["subtask_count"] == 6
    assert "updated_at" in status

    # 2. test_plan.json exists and parses
    plan_file = workspace / "test_plan.json"
    assert plan_file.exists()
    plan = json.loads(plan_file.read_text())

    # 3. Top-level shape — Gen-Functional looks for these
    assert plan["feature"]
    assert plan["workflow_type"] == "feature"
    assert plan["status"] == "in_progress"

    # 4. One phase per AC
    assert len(plan["phases"]) == 3
    phase_names = [p["name"] for p in plan["phases"]]
    for ac in ("AC#1", "AC#2", "AC#3"):
        assert any(ac in n for n in phase_names), f"missing {ac}"

    # 5. Every subtask has the schema Gen-Functional consumes
    all_subtasks = [s for p in plan["phases"] for s in p["subtasks"]]
    assert len(all_subtasks) == 6
    for s in all_subtasks:
        assert s["lane"] == "functional"
        assert s["status"] == "pending"
        assert s["target"]
        assert s["rationale"]
        assert s["files_to_create"]
        assert s["verification"]["type"] == "command"
        assert "pytest" in s["verification"]["command"]
        # Target points at a real symbol in the fixture project tree
        assert "::" in s["target"]
        path_part = s["target"].split("::")[0]
        assert (project_dir / path_part).exists(), \
            f"subtask {s['id']!r} target {path_part} not in fixture project tree"


@pytest.mark.asyncio
async def test_pipeline_handles_truncation_against_fixture(
    workspace: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with a realistic-shaped spec, if the agent over-emits we truncate."""
    class _FakeAsyncCM:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
    async def _resolve(*a, **kw): return _FakeAsyncCM()
    async def _invoke(client, prompt, spec_dir_arg, verbose):
        # 50 subtasks across one phase
        plan = {
            "feature": "huge", "workflow_type": "feature", "services_involved": [],
            "phases": [{
                "phase": 1, "name": "everything", "type": "implementation",
                "subtasks": [
                    {
                        "id": f"s{i}", "description": "x", "status": "pending",
                        "lane": "functional", "target": "app/auth/session.py::get_session",
                        "rationale": f"AC#1 extra {i}",
                        "files_to_create": [f"tests/test_{i}.py"],
                        "verification": {
                            "type": "command", "command": f"pytest tests/test_{i}.py",
                            "expected": "exit 0",
                        },
                    } for i in range(50)
                ],
                "parallel_safe": False,
            }],
            "final_acceptance": [], "status": "in_progress", "planStatus": "pending",
        }
        (spec_dir_arg / "test_plan.json").write_text(json.dumps(plan))
        return "complete", "mock response", {}

    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)

    ok = await run_planner(workspace, project_dir)
    assert ok is True
    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["subtask_count"] == 30
    assert any("truncated to 30" in w for w in status.get("planner_warnings", []))


@pytest.mark.asyncio
async def test_pipeline_idempotent_when_replan_completes(
    workspace: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
    mock_sdk_realistic,
) -> None:
    """Initial planning + a follow-up replan land cleanly side-by-side."""
    # Initial planning lands a 3-AC plan via mock_sdk_realistic.
    await run_planner(workspace, project_dir, mode="initial")
    plan_before = json.loads((workspace / "test_plan.json").read_text())
    phases_before = len(plan_before["phases"])

    # Gen-Functional rejects the first subtask — simulate.
    (workspace / "context" / "replan_request.json").write_text(json.dumps({
        "subtask_id": "ac1-login-sets-24h-expiry",
        "reason": "hallucinated import: time.timezone",
        "failed_target": "app/auth/login.py::nonexistent",
    }))

    # Replan: mock the agent emitting the existing plan + a new replan-1 phase.
    async def _replan_invoke(client, prompt, spec_dir_arg, verbose):
        new_plan = json.loads((spec_dir_arg / "test_plan.json").read_text())
        new_plan["phases"].append({
            "phase": phases_before + 1,
            "name": "replan-1",
            "type": "implementation",
            "subtasks": [{
                "id": "ac1-login-sets-24h-expiry-r1",
                "description": "Verify login_user sets expires_at via datetime.timedelta(hours=24)",
                "status": "pending",
                "lane": "functional",
                "target": "app/auth/login.py::login_user",
                "rationale": "Replan of 'ac1-login-sets-24h-expiry': original used hallucinated import; this uses datetime.timedelta",
                "files_to_create": ["tests/test_login_expiry_v2.py"],
                "verification": {
                    "type": "command",
                    "command": "pytest tests/test_login_expiry_v2.py",
                    "expected": "exit 0",
                },
            }],
            "parallel_safe": False,
        })
        (spec_dir_arg / "test_plan.json").write_text(json.dumps(new_plan))
        return "complete", "mock", {}
    monkeypatch.setattr("agents.planner._invoke_session", _replan_invoke)

    ok = await run_planner(workspace, project_dir, mode="replan")
    assert ok is True

    plan_after = json.loads((workspace / "test_plan.json").read_text())
    assert len(plan_after["phases"]) == phases_before + 1
    # Original subtask's replan_count bumped
    s = plan_after["phases"][0]["subtasks"][0]
    assert s["id"] == "ac1-login-sets-24h-expiry"
    assert s["replan_count"] == 1

    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["last_replan_for"] == "ac1-login-sets-24h-expiry"
    assert status["last_replan_count"] == 1
    assert status["last_replan_stuck"] is False
