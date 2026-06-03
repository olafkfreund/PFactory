"""End-to-end integration test: Planner → Gen-Functional → Evaluator → Triager —
Task 8 (#9) commit 6.

Drives ALL FOUR upstream agents end-to-end against the realistic
``tests/fixtures/planner_smoke/`` fixture (3 acceptance criteria,
6 functional subtasks, real project_tree).

Pairs with:
  - test_planner_integration.py       (Planner alone)
  - test_gen_functional_integration.py (Planner → Gen-Functional)
  - test_evaluator_integration.py     (Planner → Gen-Functional → Evaluator)
  - THIS file                         (full four-stage chain)

This is the LAST integration test in the MVP — completes the
"walking skeleton" pipeline. After this lands, a realistic feature
spec drives through 4 LLM-backed agents producing committed test
files + a PR comment body (both dry-run by default).

What's mocked vs. real:
  - SDK seams (Planner, Gen-Functional, Evaluator)  → MOCKED
  - Evaluator's runner_fn (stability + mutation)    → MOCKED
  - Triager + ALL its primitives                    → REAL
    (dedup, rank, report rendering, git_writer dry-run, pr_comment dry-run)
"""

from __future__ import annotations

import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest
from agents.evaluator import run_evaluator
from agents.gen_functional import run_gen_functional
from agents.planner import run_planner
from agents.triager import (
    _BG_TRIAGER_TASKS,  # noqa: F401
    run_triager,
)

FIXTURE = Path(__file__).parent / "fixtures" / "planner_smoke"


# ── Workspace fixture (mirrors evaluator integration) ────────────────


@pytest.fixture(autouse=True)
def _disable_auto_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin all four chain envs off — drive each agent explicitly."""
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_EVALUATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_TRIAGE", "0")
    # Triager side-effect envs OFF (dry-run for both git + gh)
    monkeypatch.delenv("PFACTORY_TRIAGER_GIT_WRITE", raising=False)
    monkeypatch.delenv("PFACTORY_TRIAGER_PR_COMMENT", raising=False)


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


# ── Realistic plan + sources (reused shape from evaluator integration)


_SUBTASK_IDS = [
    "ac1-login-sets-24h-expiry",
    "ac1-login-preserves-existing-fields",
    "ac2-expired-returns-none",
    "ac2-expired-removed-from-store",
    "ac3-refresh-within-grace",
    "ac3-refresh-outside-grace-noop",
]


def _realistic_plan_json() -> str:
    return json.dumps({
        "feature": "Add session expiry to the auth module",
        "workflow_type": "feature",
        "services_involved": ["backend"],
        "phases": [
            {
                "phase": 1, "name": "AC#1", "type": "implementation",
                "subtasks": [
                    {
                        "id": _SUBTASK_IDS[0],
                        "description": "login_user sets expires_at",
                        "status": "pending", "lane": "functional",
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1",
                        "files_to_create": ["tests/test_login_expiry.py"],
                        "verification": {"type": "command", "command": "pytest tests/test_login_expiry.py"},
                    },
                    {
                        "id": _SUBTASK_IDS[1],
                        "description": "Session shape unchanged",
                        "status": "pending", "lane": "functional",
                        "target": "app/auth/login.py::login_user",
                        "rationale": "AC#1 regression",
                        "files_to_create": ["tests/test_login_shape.py"],
                        "verification": {"type": "command", "command": "pytest tests/test_login_shape.py"},
                    },
                ],
                "parallel_safe": False,
            },
            {
                "phase": 2, "name": "AC#2", "type": "implementation",
                "subtasks": [
                    {
                        "id": _SUBTASK_IDS[2],
                        "description": "get_session None on expired",
                        "status": "pending", "lane": "functional",
                        "target": "app/auth/session.py::get_session",
                        "rationale": "AC#2",
                        "files_to_create": ["tests/test_get_session_expired.py"],
                        "verification": {"type": "command", "command": "pytest tests/test_get_session_expired.py"},
                    },
                    {
                        "id": _SUBTASK_IDS[3],
                        "description": "get_session removes expired",
                        "status": "pending", "lane": "functional",
                        "target": "app/auth/session.py::get_session",
                        "rationale": "AC#2 store",
                        "files_to_create": ["tests/test_get_session_removes.py"],
                        "verification": {"type": "command", "command": "pytest tests/test_get_session_removes.py"},
                    },
                ],
                "parallel_safe": False,
            },
            {
                "phase": 3, "name": "AC#3", "type": "implementation",
                "subtasks": [
                    {
                        "id": _SUBTASK_IDS[4],
                        "description": "refresh within grace",
                        "status": "pending", "lane": "functional",
                        "target": "app/auth/session.py::refresh_session",
                        "rationale": "AC#3 within",
                        "files_to_create": ["tests/test_refresh_within_grace.py"],
                        "verification": {"type": "command", "command": "pytest tests/test_refresh_within_grace.py"},
                    },
                    {
                        "id": _SUBTASK_IDS[5],
                        "description": "refresh noop outside",
                        "status": "pending", "lane": "functional",
                        "target": "app/auth/session.py::refresh_session",
                        "rationale": "AC#3 outside",
                        "files_to_create": ["tests/test_refresh_outside_grace.py"],
                        "verification": {"type": "command", "command": "pytest tests/test_refresh_outside_grace.py"},
                    },
                ],
                "parallel_safe": True,
            },
        ],
        "final_acceptance": [],
        "status": "in_progress",
        "planStatus": "pending",
    })


# Six realistic test sources — each imports real symbols from the
# fixture project_tree so the Evaluator's preflight + flake_risk_lint
# pass for real.
_SOURCES: dict[str, str] = {
    _SUBTASK_IDS[0]: textwrap.dedent('''
        """AC#1: login_user is callable."""
        from app.auth.login import login_user

        def test_login_expiry_callable():
            assert callable(login_user)
    ''').lstrip(),
    _SUBTASK_IDS[1]: textwrap.dedent('''
        """AC#1: Session dataclass shape."""
        from app.auth.session import Session

        def test_session_dataclass():
            s = Session(id="x", user_id="u", email="e@x.com", created_at="2026-01-01")
            assert s.expires_at is None
    ''').lstrip(),
    _SUBTASK_IDS[2]: textwrap.dedent('''
        """AC#2: get_session for unknown id."""
        from app.auth.session import get_session

        def test_get_session_unknown_returns_none():
            assert get_session("nonexistent") is None
    ''').lstrip(),
    _SUBTASK_IDS[3]: textwrap.dedent('''
        """AC#2: get_session uses _STORE."""
        from app.auth.session import _STORE, Session, get_session

        def test_get_session_uses_store():
            s = Session(id="s1", user_id="u", email="e", created_at="2026-01-01")
            _STORE["s1"] = s
            try:
                assert get_session("s1") is s
            finally:
                _STORE.pop("s1", None)
    ''').lstrip(),
    _SUBTASK_IDS[4]: textwrap.dedent('''
        """AC#3: grace window positive."""
        from app.auth.session import GRACE_WINDOW_MIN, refresh_session

        def test_grace_positive():
            assert GRACE_WINDOW_MIN > 0
            assert callable(refresh_session)
    ''').lstrip(),
    _SUBTASK_IDS[5]: textwrap.dedent('''
        """AC#3: TTL is 24 hours."""
        from app.auth.session import SESSION_TTL_HOURS, refresh_session

        def test_ttl_24h():
            assert SESSION_TTL_HOURS == 24
            assert callable(refresh_session)
    ''').lstrip(),
}


# ── Mock installers ────────────────────────────────────────────────────


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
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()
    async def _invoke(_client, prompt, spec_dir_arg, _verbose):
        m = re.search(r"Subtask:\s*`([^`]+)`", prompt)
        assert m is not None
        sid = m.group(1)
        source = _SOURCES[sid]
        plan = json.loads((spec_dir_arg / "test_plan.json").read_text())
        target_path = None
        for ph in plan["phases"]:
            for st in ph["subtasks"]:
                if st["id"] == sid:
                    target_path = st["files_to_create"][0]
                    break
            if target_path:
                break
        out = spec_dir_arg / target_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(source)
        return "complete", "ok", {}
    monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
    monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)


def _install_evaluator_runner_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeRes:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""
    def _runner(test_file, _pd, _seed):
        # Mutants → fail (KILLED); originals → pass
        return _FakeRes(1 if "mutants" in str(test_file) else 0)
    def _resolve(_sd, _pd): return _runner
    monkeypatch.setattr("agents.evaluator._resolve_runner_fn", _resolve)


def _install_evaluator_sdk_mock(
    monkeypatch: pytest.MonkeyPatch,
    verdict_for_id,
) -> None:
    """Mock Evaluator SDK to emit verdicts per the supplier function."""
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()

    async def _invoke(_client, prompt, spec_dir_arg, _verbose):
        ids = re.findall(r"### Test `([^`]+)`", prompt)
        doc = {
            "evaluator_version": "task7-commit5",
            "mode": "initial",
            "generated_at": "2026-05-28T00:00:00+00:00",
            "verdicts": [
                {
                    "test_id": tid,
                    "test_file": f"tests/test_{tid}.py",
                    "verdict": verdict_for_id(tid),
                    "reasons": [f"verdict for {tid}"],
                    "signals_summary": {
                        "coverage_delta_pct": 1.5,
                        "stability": "stable",
                        "mutation": "killed",
                        "lint_promotion": "no findings",
                    },
                    "semantic_relevance": "high",
                    "semantic_notes": "matches rationale",
                }
                for tid in ids
            ],
        }
        # IMPORTANT: write to the file paths the TRIAGER will read,
        # not just the IDs. The Triager looks for spec_dir/<test_file>,
        # so the evaluator's verdicts need test_file pointing at the
        # actual generated test paths.
        plan = json.loads((spec_dir_arg / "test_plan.json").read_text())
        id_to_file = {
            st["id"]: st["files_to_create"][0]
            for ph in plan["phases"]
            for st in ph["subtasks"]
        }
        for v in doc["verdicts"]:
            v["test_file"] = id_to_file.get(v["test_id"], v["test_file"])

        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps(doc, indent=2)
        )
        return "complete", "ok", {}

    monkeypatch.setattr("agents.evaluator._resolve_evaluator_client", _resolve)
    monkeypatch.setattr("agents.evaluator._invoke_session", _invoke)


# ── Integration tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_chain_to_triager_happy(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four agents run cleanly. 6 subtasks → 6 verdicts (all accept) →
    6 committed in triage report, dry-run git_writer + pr_comment_body
    on disk (no PR number in source.json)."""
    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)
    _install_evaluator_runner_mock(monkeypatch)
    _install_evaluator_sdk_mock(monkeypatch, verdict_for_id=lambda _tid: "accept")

    # Run all four agents
    assert await run_planner(workspace, project_dir, mode="initial") is True
    assert await run_gen_functional(workspace, project_dir, mode="initial") is True
    assert await run_evaluator(workspace, project_dir, mode="initial") is True
    assert await run_triager(workspace, project_dir, mode="initial") is True

    # ── Triager terminal state ────────────────────────────────────
    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "triaged"
    assert status["committed_count"] == 6
    assert status["flagged_count"] == 0
    assert status["rejected_count"] == 0
    assert status["dedup_collision_count"] == 0

    # ── Triage report files ──────────────────────────────────────
    report_json = json.loads(
        (workspace / "findings" / "triage_report.json").read_text()
    )
    assert report_json["summary"]["committed_count"] == 6
    assert len(report_json["committed"]) == 6
    seen_ids = {c["test_id"] for c in report_json["committed"]}
    assert seen_ids == set(_SUBTASK_IDS)

    report_md = (workspace / "findings" / "triage_report.md").read_text()
    assert "# Triage Report" in report_md
    for sid in _SUBTASK_IDS:
        assert sid in report_md

    # ── git_writer fired dry-run ─────────────────────────────────
    gw = status["git_writer"]
    assert gw["skipped"] is False
    assert gw["dry_run"] is True
    assert gw["ok"] is True
    # 6 test files staged
    assert len(gw["committed_paths"]) == 6
    # 5 git argvs: verify, checkout, add, commit, rev-parse HEAD
    assert len(gw["argv_log"]) == 5

    # ── pr_comment skipped (no PR number) — body written to disk ─
    pc = status["pr_comment"]
    assert pc["skipped"] is True
    assert "no PR number" in pc["reason"]
    body_md = workspace / "findings" / "pr_comment_body.md"
    assert body_md.exists()
    assert "# Triage Report" in body_md.read_text()


@pytest.mark.asyncio
async def test_full_chain_with_pr_number_dry_runs_gh(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When source.json has a pr_number, the pr_comment helper runs
    in dry-run mode and records the argv."""
    # Add pr_number + repo_slug
    src = json.loads((workspace / "context" / "source.json").read_text())
    src["pr_number"] = 42
    src["repo_slug"] = "olafkfreund/AIFactory"
    (workspace / "context" / "source.json").write_text(json.dumps(src))

    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)
    _install_evaluator_runner_mock(monkeypatch)
    _install_evaluator_sdk_mock(monkeypatch, verdict_for_id=lambda _tid: "accept")

    await run_planner(workspace, project_dir)
    await run_gen_functional(workspace, project_dir)
    await run_evaluator(workspace, project_dir)
    await run_triager(workspace, project_dir)

    status = json.loads((workspace / "status.json").read_text())
    pc = status["pr_comment"]
    assert pc["skipped"] is False
    assert pc["dry_run"] is True
    assert pc["ok"] is True
    # argv has both -R + --body-file -
    assert "42" in pc["argv"]
    assert "-R" in pc["argv"]
    assert "olafkfreund/AIFactory" in pc["argv"]
    assert "--body-file" in pc["argv"]
    assert pc["body_bytes"] > 0


@pytest.mark.asyncio
async def test_full_chain_with_mixed_verdicts_buckets_correctly(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mix of accept/flag/reject verdicts → triager buckets each
    correctly and the report reflects the breakdown."""
    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)
    _install_evaluator_runner_mock(monkeypatch)

    def _mixed(tid: str) -> str:
        idx = _SUBTASK_IDS.index(tid)
        # 0,1 → accept ; 2,3 → flag ; 4,5 → reject
        return ("accept", "accept", "flag", "flag", "reject", "reject")[idx]
    _install_evaluator_sdk_mock(monkeypatch, verdict_for_id=_mixed)

    await run_planner(workspace, project_dir)
    await run_gen_functional(workspace, project_dir)
    await run_evaluator(workspace, project_dir)
    assert await run_triager(workspace, project_dir) is True

    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "triaged"
    assert status["committed_count"] == 2
    assert status["flagged_count"] == 2
    assert status["rejected_count"] == 2
    # git_writer dry-runs 4 files (2 accept + 2 flag), not the 2 rejects
    gw = status["git_writer"]
    assert gw["skipped"] is False
    assert len(gw["committed_paths"]) == 4
