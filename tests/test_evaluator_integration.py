"""End-to-end integration test: Planner → Gen-Functional → Evaluator —
Task 7 (#8) commit 6.

Drives all THREE upstream agents end-to-end against the realistic
``tests/fixtures/planner_smoke/`` fixture (3 ACs, 6 functional
subtasks, real project_tree with import-resolvable symbols).

Pairs with:
  - test_planner_integration.py  (Planner alone)
  - test_gen_functional_integration.py  (Planner → Gen-Functional)
  - THIS file  (Planner → Gen-Functional → Evaluator)

The Triager (Task 8) will pick up where this leaves off — reading the
verdicts.json this test verifies the Evaluator emits.

What's mocked vs. real:
  - SDK seams (Planner, Gen-Functional, Evaluator)   → MOCKED
  - Runner_fn seam (Evaluator's stability + mutation) → MOCKED
  - All four numeric primitives (coverage_delta,
    stability_runner verdict logic, mutate_probe AST,
    flake_risk_lint + lint_promotion)                → RUN FOR REAL
  - Verdicts.json schema validation                  → RUN FOR REAL

The companion failure-mode cases (verdicts validation rejects, etc.)
are unit-covered in test_evaluator.py. Here we focus on the realistic
happy chain — proving the three agents wire end-to-end.
"""

from __future__ import annotations

import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest
from agents.evaluator import (
    _BG_EVALUATOR_TASKS,  # noqa: F401 — GC anchor importable
    run_evaluator,
)
from agents.gen_functional import run_gen_functional
from agents.planner import run_planner

FIXTURE = Path(__file__).parent / "fixtures" / "planner_smoke"


# ── Workspace fixture (mirrors test_gen_functional_integration) ──────


@pytest.fixture(autouse=True)
def _disable_auto_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin all three chain env vars off — we drive each agent explicitly
    here so we can assert ordering and intermediate state."""
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_EVALUATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_TRIAGE", "0")


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


# ── Realistic Planner plan + Gen-Functional sources (same as commit-6
#    of Task 6 — reusing the proven shape) ────────────────────────────


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
                "phase": 1,
                "name": "AC#1: login_user sets expires_at to +24h",
                "type": "implementation",
                "subtasks": [
                    {
                        "id": _SUBTASK_IDS[0],
                        "description": "login_user returns a session with expires_at exactly 24h after creation",
                        "status": "pending", "lane": "functional",
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
                        "id": _SUBTASK_IDS[1],
                        "description": "Session shape unchanged",
                        "status": "pending", "lane": "functional",
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
                        "id": _SUBTASK_IDS[2],
                        "description": "get_session returns None for expired",
                        "status": "pending", "lane": "functional",
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
                        "id": _SUBTASK_IDS[3],
                        "description": "get_session removes expired entry",
                        "status": "pending", "lane": "functional",
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
                        "id": _SUBTASK_IDS[4],
                        "description": "refresh_session extends expiry within 5min",
                        "status": "pending", "lane": "functional",
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
                        "id": _SUBTASK_IDS[5],
                        "description": "refresh_session unmodified outside grace",
                        "status": "pending", "lane": "functional",
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


_SOURCES_BY_SUBTASK_ID: dict[str, str] = {
    _SUBTASK_IDS[0]: textwrap.dedent('''
        """AC#1: login_user sets expires_at to +24h from creation."""
        from app.auth.login import login_user

        def test_login_expiry_is_callable():
            assert callable(login_user)
    ''').lstrip(),
    _SUBTASK_IDS[1]: textwrap.dedent('''
        """AC#1 regression: Session shape unchanged."""
        from app.auth.session import Session

        def test_session_dataclass_has_expected_fields():
            s = Session(id="x", user_id="u", email="e@x.com", created_at="2026-01-01")
            assert s.id == "x"
            assert s.user_id == "u"
            assert s.email == "e@x.com"
            assert s.expires_at is None
    ''').lstrip(),
    _SUBTASK_IDS[2]: textwrap.dedent('''
        """AC#2: get_session returns None for expired session."""
        from app.auth.session import get_session

        def test_get_session_returns_none_for_unknown_id():
            assert get_session("nonexistent") is None
    ''').lstrip(),
    _SUBTASK_IDS[3]: textwrap.dedent('''
        """AC#2: get_session removes expired entry from _STORE."""
        from app.auth.session import _STORE, Session, get_session

        def test_get_session_uses_store():
            sentinel = Session(id="s1", user_id="u1", email="e@x.com", created_at="2026-01-01")
            _STORE["s1"] = sentinel
            try:
                assert get_session("s1") is sentinel
            finally:
                _STORE.pop("s1", None)
    ''').lstrip(),
    _SUBTASK_IDS[4]: textwrap.dedent('''
        """AC#3: refresh_session extends expiry within grace window."""
        from app.auth.session import GRACE_WINDOW_MIN, refresh_session

        def test_grace_window_is_positive():
            assert GRACE_WINDOW_MIN > 0
            assert callable(refresh_session)
    ''').lstrip(),
    _SUBTASK_IDS[5]: textwrap.dedent('''
        """AC#3: refresh_session is a no-op outside the grace window."""
        from app.auth.session import SESSION_TTL_HOURS, refresh_session

        def test_session_ttl_is_24_hours():
            assert SESSION_TTL_HOURS == 24
            assert callable(refresh_session)
    ''').lstrip(),
}


# ── Mocks ──────────────────────────────────────────────────────────────


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
        assert m is not None, "subtask_id not in gen_functional prompt"
        sid = m.group(1)
        source = _SOURCES_BY_SUBTASK_ID.get(sid)
        assert source is not None, f"no fixture source for {sid!r}"

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
        out.write_text(source)
        return "complete", "ok", {}

    monkeypatch.setattr("agents.gen_functional._resolve_client", _resolve)
    monkeypatch.setattr("agents.gen_functional._invoke_session", _invoke)


def _install_evaluator_runner_mock(
    monkeypatch: pytest.MonkeyPatch, *, mutation_returncode: int = 1,
) -> None:
    """Replace evaluator's runner_fn seam with one that:
      - Returns 0 for stability calls (3× re-run of the original test)
      - Returns ``mutation_returncode`` for the mutation probe call
        (default 1 = KILLED — mutation was caught)

    Distinguishes by inspecting the path: mutation writes to
    spec_dir/findings/mutants/<id>.py; stability uses the original
    spec_dir/tests/*.py.
    """
    class _FakeResult:
        def __init__(self, rc: int):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def _runner(test_file, _project_dir, _seed):
        if "mutants" in str(test_file):
            return _FakeResult(mutation_returncode)
        return _FakeResult(0)

    def _resolve(_spec_dir, _project_dir):
        return _runner

    monkeypatch.setattr("agents.evaluator._resolve_runner_fn", _resolve)


def _install_evaluator_sdk_mock(
    monkeypatch: pytest.MonkeyPatch,
    verdict_for_id,  # Callable[[str], str]   maps test_id → verdict
    captured_prompt: list[str] | None = None,
) -> None:
    """Mock the Evaluator SDK seams; on _invoke_session, parse the
    prompt to discover which test_ids are in this batch and write
    a verdicts.json with one verdict per id."""
    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()

    async def _invoke(_client, prompt, spec_dir_arg, _verbose):
        if captured_prompt is not None:
            captured_prompt.append(prompt)
        # The helper emits one "### Test `<id>`" sub-block per test.
        test_ids = re.findall(r"### Test `([^`]+)`", prompt)
        doc = {
            "evaluator_version": "task7-commit5",
            "mode": "initial",
            "generated_at": "2026-05-28T00:00:00+00:00",
            "verdicts": [
                {
                    "test_id": tid,
                    "test_file": f"tests/test_{tid}.py",
                    "verdict": verdict_for_id(tid),
                    "reasons": ["realistic test, signals green"],
                    "signals_summary": {
                        "coverage_delta_pct": 0.0,
                        "coverage_new_lines": 0,
                        "stability": "stable",
                        "mutation": "killed",
                        "lint_promotion": "no findings",
                    },
                    "semantic_relevance": "high",
                    "semantic_notes": "test matches rationale",
                }
                for tid in test_ids
            ],
        }
        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps(doc, indent=2)
        )
        return "complete", "ok", {}

    monkeypatch.setattr("agents.evaluator._resolve_evaluator_client", _resolve)
    monkeypatch.setattr("agents.evaluator._invoke_session", _invoke)


# ── Integration tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_chain_planner_gen_functional_evaluator_happy(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realistic end-to-end:
      Planner emits 6 subtasks → Gen-Functional writes 6 real tests →
      Evaluator builds 6 signal bundles, invokes SDK, validates a
      verdicts.json with 6 verdicts → status.json reaches 'evaluated'.
    """
    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)
    _install_evaluator_runner_mock(monkeypatch, mutation_returncode=1)  # KILLED
    captured_prompts: list[str] = []
    _install_evaluator_sdk_mock(
        monkeypatch,
        verdict_for_id=lambda _tid: "accept",
        captured_prompt=captured_prompts,
    )

    # ─── Planner phase ──────────────────────────────────────────────
    assert await run_planner(workspace, project_dir, mode="initial") is True
    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "planned"
    assert status["subtask_count"] == 6

    # ─── Gen-Functional phase ───────────────────────────────────────
    assert await run_gen_functional(workspace, project_dir, mode="initial") is True
    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "generated"
    assert status["tests_generated"] == 6

    # ─── Evaluator phase ────────────────────────────────────────────
    assert await run_evaluator(workspace, project_dir, mode="initial") is True

    # 1. status.json terminal state
    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "evaluated"
    assert status["phase"] == "evaluator_complete"
    assert status["verdicts_count"] == 6
    assert status["tests_evaluated"] == 6

    # 2. verdicts.json: 6 well-formed verdicts
    verdicts = json.loads(
        (workspace / "findings" / "verdicts.json").read_text()
    )
    assert verdicts["evaluator_version"] == "task7-commit5"
    assert verdicts["mode"] == "initial"
    assert len(verdicts["verdicts"]) == 6
    seen_ids = {v["test_id"] for v in verdicts["verdicts"]}
    assert seen_ids == set(_SUBTASK_IDS)
    for v in verdicts["verdicts"]:
        assert v["verdict"] == "accept"
        assert v["semantic_relevance"] == "high"
        assert v["signals_summary"]["stability"] == "stable"

    # 3. Evaluator prompt actually carried EVALUATOR CONTEXT + all 6 ids
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "EVALUATOR CONTEXT" in prompt
    for sid in _SUBTASK_IDS:
        assert sid in prompt

    # 4. Mutant artefacts were produced (mutate_probe ran for real)
    mutants_dir = workspace / "findings" / "mutants"
    assert mutants_dir.exists()
    mutant_files = list(mutants_dir.glob("*.py"))
    # mutate_probe.write_mutant_to was called for each test the
    # mutator could mutate (every test in our fixture has an
    # Eq compare or a bool/int Constant → all 6 mutate)
    assert len(mutant_files) >= 1, "mutate_probe didn't write any mutants"


@pytest.mark.asyncio
async def test_chain_with_evaluator_mixed_verdicts(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same chain but the Evaluator emits a mix of accept/reject/flag
    verdicts — confirming the validator accepts all three values."""
    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)
    _install_evaluator_runner_mock(monkeypatch)

    def _mixed_verdict(tid: str) -> str:
        # Map first two to accept, next two to reject, last two to flag
        idx = _SUBTASK_IDS.index(tid)
        return ("accept", "accept", "reject", "reject", "flag", "flag")[idx]

    _install_evaluator_sdk_mock(monkeypatch, verdict_for_id=_mixed_verdict)

    await run_planner(workspace, project_dir, mode="initial")
    await run_gen_functional(workspace, project_dir, mode="initial")
    assert await run_evaluator(workspace, project_dir, mode="initial") is True

    verdicts = json.loads(
        (workspace / "findings" / "verdicts.json").read_text()
    )
    verdict_values = {v["verdict"] for v in verdicts["verdicts"]}
    assert verdict_values == {"accept", "reject", "flag"}

    status = json.loads((workspace / "status.json").read_text())
    assert status["status"] == "evaluated"
    assert status["verdicts_count"] == 6


@pytest.mark.asyncio
async def test_chain_aborts_at_evaluator_invalid_verdicts(
    workspace: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Evaluator's validator catches a malformed verdicts.json
    even in a full pipeline. Upstream agents still complete cleanly;
    only the Evaluator phase is marked failed."""
    _install_planner_mock(monkeypatch)
    _install_gen_functional_mock(monkeypatch)
    _install_evaluator_runner_mock(monkeypatch)

    class _CM:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
    async def _resolve(*_a, **_kw): return _CM()
    async def _invoke(_client, _prompt, spec_dir_arg, _verbose):
        # Emit a doc missing the 'verdicts' array
        (spec_dir_arg / "findings" / "verdicts.json").write_text(
            json.dumps({"evaluator_version": "x", "no_verdicts_here": True})
        )
        return "complete", "ok", {}
    monkeypatch.setattr("agents.evaluator._resolve_evaluator_client", _resolve)
    monkeypatch.setattr("agents.evaluator._invoke_session", _invoke)

    await run_planner(workspace, project_dir, mode="initial")
    await run_gen_functional(workspace, project_dir, mode="initial")

    # Pre-eval: gen_functional completed cleanly
    status_pre = json.loads((workspace / "status.json").read_text())
    assert status_pre["status"] == "generated"
    assert status_pre["tests_generated"] == 6

    assert await run_evaluator(workspace, project_dir, mode="initial") is False

    # Post-eval: status reflects the validator's rejection
    status_post = json.loads((workspace / "status.json").read_text())
    assert status_post["status"] == "evaluator_failed"
    assert status_post["phase"] == "evaluator_invalid_verdicts"
    assert "missing 'verdicts' array" in status_post["evaluator_error"]

    # Generated test files are still present — only the Evaluator's
    # verdicts.json is rejected, not the upstream output
    for sid in _SUBTASK_IDS:
        plan = json.loads((workspace / "test_plan.json").read_text())
        st = next(s for p in plan["phases"] for s in p["subtasks"] if s["id"] == sid)
        path = workspace / st["files_to_create"][0]
        assert path.exists()
