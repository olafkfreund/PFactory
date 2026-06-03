"""End-to-end integration test: Planner → Gen-Functional — Task 6 (#7) commit 6.

Pairs with ``tests/test_planner_integration.py``. Where that file
verifies the *Planner* drives a realistic 3-AC spec to a valid plan,
this one *picks up where it leaves off* — feeds the same realistic
plan into the Gen-Functional agent and asserts the FULL handoff
contract holds:

  Planner emits test_plan.json (6 subtasks, lane=functional)
       ↓ persisted via ImplementationPlan.save
  Gen-Functional reads test_plan.json
       ↓ per subtask: SDK session writes a pytest file
       ↓ pre-flight static check (subprocess against project_tree env)
       ↓ flake-risk lint (AST scan)
       ↓ mark subtask completed; tests_generated++
  status.json → status=generated, tests_generated=6

Both guardrails run **unmocked** against the realistic fixture
project tree (`tests/fixtures/planner_smoke/project_tree/`) — so the
generated test sources import real symbols and the AST scan walks
real code. Only the SDK seam is mocked.

The companion rejection-loop case is unit-covered in
``tests/test_gen_functional.py::test_full_chain_rejection_loops_back_to_planner_replan``;
here we focus on the realistic *happy* end-to-end shape.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest
from agents.gen_functional import (
    _BG_GEN_FUNCTIONAL_TASKS,  # noqa: F401  — keep the GC anchor importable
    run_gen_functional,
)
from agents.planner import run_planner

FIXTURE = Path(__file__).parent / "fixtures" / "planner_smoke"


# ── Workspace fixture (mirrors test_planner_integration) ───────────────


@pytest.fixture(autouse=True)
def _disable_auto_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin BOTH chain env vars off — we drive each phase explicitly here
    so we can assert ordering and intermediate state."""
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("PFACTORY_AUTO_EVALUATE", "0")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    spec_dir = tmp_path / "workspaces" / "demo" / "specs" / "042-session-expiry"
    spec_dir.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (spec_dir / sub).mkdir()

    (spec_dir / "status.json").write_text(json.dumps({
        "task_id": "042-session-expiry",
        "project_id": "demo",
        "spec_id": "042-session-expiry",
        "status": "pending",
        "phase": "created",
    }))

    shutil.copy(FIXTURE / "aifactory_spec.md", spec_dir / "context" / "aifactory_spec.md")
    shutil.copy(FIXTURE / "aifactory_plan.json", spec_dir / "context" / "aifactory_plan.json")
    shutil.copy(FIXTURE / "diff.patch", spec_dir / "context" / "diff.patch")
    (spec_dir / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
        "spec_id": "042-session-expiry",
        "branch": "feature/session-expiry",
        "base_ref": "main",
        "snapshotted_at": "2026-05-28T00:00:00+00:00",
        "has_spec_md": True,
        "has_plan_json": True,
        "has_diff_patch": True,
        "warnings": [],
    }))
    return spec_dir


@pytest.fixture
def project_dir() -> Path:
    return FIXTURE / "project_tree"


# ── Realistic plan (same as test_planner_integration) ─────────────────


def _realistic_plan_json() -> str:
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
                        "rationale": "AC#1",
                        "files_to_create": ["tests/test_login_expiry.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_login_expiry.py",
                            "expected": "exit 0",
                        },
                    },
                    {
                        "id": "ac1-login-preserves-existing-fields",
                        "description": "Session shape unchanged",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1 regression guard",
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
                        "description": "get_session returns None for expired session",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::get_session",
                        "rationale": "AC#2",
                        "files_to_create": ["tests/test_get_session_expired.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_get_session_expired.py",
                            "expected": "exit 0",
                        },
                    },
                    {
                        "id": "ac2-expired-removed-from-store",
                        "description": "get_session removes expired entry",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::get_session",
                        "rationale": "AC#2 store cleanup",
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
                        "description": "refresh_session extends expiry within 5min",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::refresh_session",
                        "rationale": "AC#3 within grace",
                        "files_to_create": ["tests/test_refresh_within_grace.py"],
                        "verification": {
                            "type": "command",
                            "command": "pytest tests/test_refresh_within_grace.py",
                            "expected": "exit 0",
                        },
                    },
                    {
                        "id": "ac3-refresh-outside-grace-noop",
                        "description": "refresh_session unmodified outside grace",
                        "status": "pending",
                        "lane": "functional",
                        "target": "app/auth/session.py::refresh_session",
                        "rationale": "AC#3 outside grace",
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
        "final_acceptance": ["All three functions behave per AC."],
        "status": "in_progress",
        "planStatus": "pending",
    })


# ── Generated test sources (realistic — imports resolve in project_tree) ──


# A test the agent emits for each subtask. Imports are REAL — they
# resolve in tests/fixtures/planner_smoke/project_tree/ — so the
# pre-flight subprocess check actually passes. Patterns are clean —
# no dict-iter, no random.choice, no time.sleep — so flake-lint passes
# too. These are the kinds of sources a competent LLM would emit.
_SOURCES_BY_SUBTASK_ID: dict[str, str] = {
    "ac1-login-sets-24h-expiry": textwrap.dedent('''
        """AC#1: login_user sets expires_at to +24h from creation."""
        from datetime import datetime, timedelta


        def test_login_expiry_set_to_24h_after_created_at():
            from app.auth.login import login_user

            assert callable(login_user)
    ''').lstrip(),
    "ac1-login-preserves-existing-fields": textwrap.dedent('''
        """AC#1 regression: Session shape unchanged."""
        from app.auth.session import Session


        def test_session_dataclass_has_expected_fields():
            s = Session(id="x", user_id="u", email="e@x.com", created_at="2026-01-01")
            assert s.id == "x"
            assert s.user_id == "u"
            assert s.email == "e@x.com"
            assert s.expires_at is None
    ''').lstrip(),
    "ac2-expired-returns-none": textwrap.dedent('''
        """AC#2: get_session returns None for expired session."""
        from app.auth.session import get_session, _STORE


        def test_get_session_returns_none_for_unknown_id():
            assert get_session("nonexistent") is None
    ''').lstrip(),
    "ac2-expired-removed-from-store": textwrap.dedent('''
        """AC#2: get_session removes expired entry from _STORE."""
        from app.auth.session import get_session, _STORE, Session


        def test_get_session_uses_store():
            assert isinstance(_STORE, dict)
            sentinel = Session(id="s1", user_id="u1", email="e@x.com", created_at="2026-01-01")
            _STORE["s1"] = sentinel
            try:
                assert get_session("s1") is sentinel
            finally:
                _STORE.pop("s1", None)
    ''').lstrip(),
    "ac3-refresh-within-grace": textwrap.dedent('''
        """AC#3: refresh_session extends expiry within grace window."""
        from app.auth.session import refresh_session, GRACE_WINDOW_MIN


        def test_grace_window_is_positive():
            assert GRACE_WINDOW_MIN > 0
            assert callable(refresh_session)
    ''').lstrip(),
    "ac3-refresh-outside-grace-noop": textwrap.dedent('''
        """AC#3: refresh_session is a no-op outside the grace window."""
        from app.auth.session import refresh_session, SESSION_TTL_HOURS


        def test_session_ttl_is_24_hours():
            assert SESSION_TTL_HOURS == 24
    ''').lstrip(),
}


# ── Mocks: Planner SDK + Gen-Functional SDK ───────────────────────────


def _install_planner_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()
    async def _invoke(_client, _prompt, spec_dir_arg, _verbose):
        (spec_dir_arg / "test_plan.json").write_text(_realistic_plan_json())
        return "complete", "ok", {}
    monkeypatch.setattr("agents.planner._resolve_planner_client", _resolve)
    monkeypatch.setattr("agents.planner._invoke_session", _invoke)


def _install_gen_functional_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock Gen-Functional SDK to write the per-subtask test source
    that matches the subtask_id from the prompt's SUBTASK CONTEXT block."""
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()

    async def _invoke(_client, prompt, spec_dir_arg, _verbose):
        # Helper format: "Subtask: `<id>` — <description>"
        import re
        m = re.search(r"Subtask:\s*`([^`]+)`", prompt)
        assert m is not None, "test fixture failed to find subtask_id in prompt"
        sid = m.group(1)
        source = _SOURCES_BY_SUBTASK_ID.get(sid)
        assert source is not None, f"no fixture source for subtask {sid!r}"

        # Mirror the agent: write to spec_dir/{files_to_create[0]}.
        # We look up the path from the persisted test_plan.json so
        # this stays in sync with the realistic plan above.
        plan = json.loads((spec_dir_arg / "test_plan.json").read_text())
        target_path = None
        for ph in plan["phases"]:
            for st in ph["subtasks"]:
                if st["id"] == sid:
                    target_path = st["files_to_create"][0]
                    break
            if target_path:
                break
        assert target_path, f"no files_to_create for {sid!r} in plan"
        out = spec_dir_arg / target_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(source)
        return "complete", "ok", {}

    monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
    monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)


# ── The integration test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_handoff_planner_to_gen_functional(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realistic end-to-end: Planner emits 6 subtasks, Gen-Functional
    consumes all 6, writes 6 test files, both guardrails pass, the plan
    records 6 completed subtasks, status.json reaches 'generated'.

    Both guardrails (preflight + flake-lint) run unmocked.
    """
    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)

    # ─── Planner phase ────────────────────────────────────────────────
    planner_ok = await run_planner(workspace, project_dir, mode="initial")
    assert planner_ok is True

    plan_path = workspace / "test_plan.json"
    plan = json.loads(plan_path.read_text())
    assert len(plan["phases"]) == 3
    pending = [
        s for p in plan["phases"] for s in p["subtasks"] if s["status"] == "pending"
    ]
    assert len(pending) == 6

    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["subtask_count"] == 6

    # ─── Gen-Functional phase ─────────────────────────────────────────
    gen_ok = await run_gen_functional(workspace, project_dir, mode="initial")
    assert gen_ok is True

    # 1. All 6 test files written under spec_dir/tests/
    expected_files = [
        "tests/test_login_expiry.py",
        "tests/test_login_shape.py",
        "tests/test_get_session_expired.py",
        "tests/test_get_session_removes.py",
        "tests/test_refresh_within_grace.py",
        "tests/test_refresh_outside_grace.py",
    ]
    for rel in expected_files:
        path = workspace / rel
        assert path.exists(), f"missing generated test {rel}"
        # Sanity: imports survived the pre-flight check, file is non-trivial
        body = path.read_text()
        assert "def test_" in body
        assert "from app.auth" in body

    # 2. test_plan.json: every subtask marked completed
    plan_after = json.loads(plan_path.read_text())
    all_subtasks = [s for p in plan_after["phases"] for s in p["subtasks"]]
    assert len(all_subtasks) == 6
    for s in all_subtasks:
        assert s["status"] == "completed", f"subtask {s['id']!r} not completed"

    # 3. status.json: generated with tests_generated=6
    status_final = json.loads((workspace / "status.json").read_text())
    assert status_final["status"] == "generated"
    assert status_final["tests_generated"] == 6
    assert status_final.get("last_rejected_subtask") is None

    # 4. No replan request was written
    assert not (workspace / "context" / "replan_request.json").exists()


@pytest.mark.asyncio
async def test_partial_pipeline_stops_at_first_rejection(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Gen-Functional's first subtask trips a guardrail, the loop stops
    and a replan_request.json lands — even on a full realistic plan.

    Drives the realistic Planner output, then has the gen_functional
    SDK mock write a flake-lint-rejecting source for the FIRST subtask.
    The remaining 5 subtasks must NOT be generated.
    """
    _install_planner_mock(monkeypatch)

    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()

    bad_source = textwrap.dedent('''
        """Trips flake-lint: dict iteration compared to list literal."""
        def test_bad():
            d = {"a": 1, "b": 2}
            assert list(d.keys()) == ["a", "b"]
    ''').lstrip()

    invocations: list[str] = []
    async def _invoke(_client, prompt, spec_dir_arg, _verbose):
        import re
        m = re.search(r"Subtask:\s*`([^`]+)`", prompt)
        assert m is not None
        sid = m.group(1)
        invocations.append(sid)

        plan = json.loads((spec_dir_arg / "test_plan.json").read_text())
        target_path = None
        for ph in plan["phases"]:
            for st in ph["subtasks"]:
                if st["id"] == sid:
                    target_path = st["files_to_create"][0]
                    break
            if target_path:
                break
        assert target_path
        out = spec_dir_arg / target_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(bad_source)
        return "complete", "ok", {}

    monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
    monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)

    # Run Planner → Gen-Functional
    await run_planner(workspace, project_dir, mode="initial")
    result = await run_gen_functional(workspace, project_dir, mode="initial")
    assert result is False  # loop stopped

    # Only ONE subtask was attempted (the rest were short-circuited)
    assert len(invocations) == 1

    # replan_request.json written
    replan_path = workspace / "context" / "replan_request.json"
    assert replan_path.exists()
    replan = json.loads(replan_path.read_text())
    assert replan["subtask_id"] == invocations[0]
    assert "flake" in replan["reason"].lower()

    # status.json: replan_needed
    status_final = json.loads((workspace / "status.json").read_text())
    assert status_final["status"] == "replan_needed"
    assert status_final["last_rejected_subtask"] == invocations[0]

    # The rejected file was cleaned up (no garbage tests/ files)
    plan_after = json.loads((workspace / "test_plan.json").read_text())
    pending_after = [
        s for p in plan_after["phases"]
        for s in p["subtasks"] if s["status"] == "pending"
    ]
    assert len(pending_after) == 6  # nothing got marked completed
