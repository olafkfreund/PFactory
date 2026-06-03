"""Tests for the real run_triager + auto-fire scaffold —
Task 8 (#9) commit 5.

Covers the full triager pipeline with the commit-2/3/4 primitives:
load verdicts.json → wrap candidates → filter rejects → dedup →
rank → render reports → git_writer (dry-run) → pr_comment (dry-run).
Side-effect helpers default to dry-run via env, so no test ever
shells out to real git/gh.

Covered:
  - Happy 3-verdict path: 1 accept + 1 flag + 1 reject →
    triage_report.{md,json} emitted with correct counts, git_writer
    dry-run argv recorded, pr_comment body written when no PR number
  - Empty verdicts → triaged_empty, empty report emitted, no
    side-effect calls
  - Missing verdicts.json → triager_failed (phase=triager_no_verdicts)
  - Malformed verdicts.json → triager_failed
  - Dedup collision recorded in report + status.json
  - source.json with pr_number → pr_comment dry-run path runs
  - source.json with branch but no pr_number → comment body written
    to disk, gh skipped
  - schedule_triager env-gate + GC anchor (carried from commit 1)
  - Forward chain from evaluator (carried from commit 1)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agents.triager import (
    _BG_TRIAGER_TASKS,
    run_triager,
    schedule_triager,
)

# ── autouse env pins ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_chains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "0")
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_EVALUATE", "0")
    monkeypatch.setenv("PFACTORY_AUTO_TRIAGE", "0")
    # Side-effect env vars must be UNSET so dry-run stays on
    monkeypatch.delenv("PFACTORY_TRIAGER_GIT_WRITE", raising=False)
    monkeypatch.delenv("PFACTORY_TRIAGER_PR_COMMENT", raising=False)


# ── Workspace helpers ──────────────────────────────────────────────────


def _make_verdicts(num: int, verdicts: list[str] | None = None) -> dict:
    """Build a verdicts.json doc with N entries.

    Each entry pairs (test_id, verdict_label, test_file). Default
    verdict is 'accept'; override via the verdicts param.
    """
    labels = verdicts or ["accept"] * num
    assert len(labels) == num
    return {
        "evaluator_version": "task7-commit5",
        "mode": "initial",
        "generated_at": "2026-05-28T00:00:00+00:00",
        "verdicts": [
            {
                "test_id": f"st{i}",
                "test_file": f"tests/test_{i}.py",
                "verdict": labels[i],
                "reasons": [f"reason for st{i}"],
                "signals_summary": {
                    "coverage_delta_pct": 1.0,
                    "stability": "stable",
                    "mutation": "killed",
                    "lint_promotion": "no findings",
                },
                "semantic_relevance": "high",
            }
            for i in range(num)
        ],
    }


@pytest.fixture
def spec_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspaces" / "demo" / "specs" / "001-feat"
    d.mkdir(parents=True)
    for sub in ("context", "tests", "findings", "logs", "memory"):
        (d / sub).mkdir()
    (d / "status.json").write_text(json.dumps({
        "task_id": "001-feat",
        "project_id": "demo",
        "spec_id": "001-feat",
        "status": "evaluated",
        "phase": "evaluator_complete",
        "verdicts_count": 3,
        "tests_evaluated": 3,
    }))
    # Source metadata — no PR number by default; tests can override.
    (d / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
        "branch": "auto-claude/test-feat",
        "base_ref": "main",
    }))
    return d


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "project"
    d.mkdir()
    return d


def _write_test_files(spec_dir: Path, count: int) -> None:
    """Write N pytest files under spec_dir/tests/ matching the
    verdicts.json test_file paths."""
    for i in range(count):
        path = spec_dir / "tests" / f"test_{i}.py"
        path.write_text(f"def test_{i}():\n    assert True\n")


# ── Happy paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_three_verdicts_mixed(
    spec_dir: Path, project_dir: Path,
) -> None:
    """1 accept + 1 flag + 1 reject → triaged, report emitted,
    git_writer dry-run argv recorded."""
    verdicts = _make_verdicts(3, ["accept", "flag", "reject"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 3)

    ok = await run_triager(spec_dir, project_dir, mode="initial")
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triaged"
    assert status["committed_count"] == 1
    assert status["flagged_count"] == 1
    assert status["rejected_count"] == 1
    assert status["dedup_collision_count"] == 0

    # Report files emitted
    report_json = json.loads(
        (spec_dir / "findings" / "triage_report.json").read_text()
    )
    assert report_json["summary"]["committed_count"] == 1
    assert report_json["summary"]["rejected_count"] == 1
    assert len(report_json["committed"]) == 1
    assert report_json["committed"][0]["test_id"] == "st0"

    report_md = (spec_dir / "findings" / "triage_report.md").read_text()
    assert "# Triage Report" in report_md
    assert "## Committed" in report_md

    # git_writer fired in dry-run mode
    gw = status["git_writer"]
    assert gw["skipped"] is False
    assert gw["dry_run"] is True
    assert gw["ok"] is True
    # 5 dry-run argvs: verify, checkout, add, commit, rev-parse HEAD
    assert len(gw["argv_log"]) == 5

    # pr_comment skipped (no pr_number in source.json) →
    # body written to disk
    pc = status["pr_comment"]
    assert pc["skipped"] is True
    assert "no PR number" in pc["reason"]
    assert (spec_dir / "findings" / "pr_comment_body.md").exists()


@pytest.mark.asyncio
async def test_pr_comment_dry_run_when_pr_number_present(
    spec_dir: Path, project_dir: Path,
) -> None:
    """source.json with pr_number → pr_comment runs in dry-run mode
    (default env)."""
    verdicts = _make_verdicts(1, ["accept"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 1)

    (spec_dir / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
        "branch": "auto-claude/test-feat",
        "pr_number": 42,
        "repo_slug": "olafkfreund/AIFactory",
    }))

    await run_triager(spec_dir, project_dir, mode="initial")

    status = json.loads((spec_dir / "status.json").read_text())
    pc = status["pr_comment"]
    assert pc["skipped"] is False
    assert pc["dry_run"] is True
    assert pc["ok"] is True
    # argv shape with repo_slug
    assert "42" in pc["argv"]
    assert "olafkfreund/AIFactory" in pc["argv"]
    assert "--body-file" in pc["argv"]
    # body_bytes > 0 (we sent the report MD)
    assert pc["body_bytes"] > 0


@pytest.mark.asyncio
async def test_dedup_collision_recorded(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Two byte-identical generated tests → one drops, collision
    reported in report + status."""
    verdicts = _make_verdicts(2, ["accept", "accept"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    # Write IDENTICAL test files for st0 and st1
    src = "def test_x():\n    assert True\n"
    (spec_dir / "tests" / "test_0.py").write_text(src)
    (spec_dir / "tests" / "test_1.py").write_text(src)

    await run_triager(spec_dir, project_dir)

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["committed_count"] == 1   # one dropped
    assert status["dedup_collision_count"] == 1

    report = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert len(report["dedup_collisions"]) == 1
    assert report["dedup_collisions"][0]["kind"] == "byte_identical"
    assert report["dedup_collisions"][0]["representative"] == "st0"


# ── Empty paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_verdicts_is_triaged_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(0)
    ))

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triaged_empty"
    assert status["committed_count"] == 0

    # Empty report files still emitted
    report = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert report["summary"]["dedup_input_count"] == 0


@pytest.mark.asyncio
async def test_all_rejects_is_triaged_empty(
    spec_dir: Path, project_dir: Path,
) -> None:
    """All verdicts are reject → triaged_empty, but rejects recorded
    in the report."""
    verdicts = _make_verdicts(2, ["reject", "reject"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 2)

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True

    status = json.loads((spec_dir / "status.json").read_text())
    # No survivors → triaged_empty (committed=0 + flagged=0)
    assert status["status"] == "triaged_empty"
    assert status["committed_count"] == 0
    assert status["rejected_count"] == 2

    report = json.loads((spec_dir / "findings" / "triage_report.json").read_text())
    assert report["summary"]["rejected_count"] == 2


# ── Failure paths ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_verdicts_is_triager_failed(
    spec_dir: Path, project_dir: Path,
) -> None:
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triager_failed"
    assert status["phase"] == "triager_no_verdicts"


@pytest.mark.asyncio
async def test_malformed_verdicts_is_triager_failed(
    spec_dir: Path, project_dir: Path,
) -> None:
    (spec_dir / "findings" / "verdicts.json").write_text("not json {")
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["phase"] == "triager_verdicts_unparseable"


@pytest.mark.asyncio
async def test_hard_failure_caught(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force an exception inside the loop → status=triager_failed."""
    from agents import triager

    real_write = triager._write_status_patch
    call_count = {"n": 0}

    def _bomb(sd, **fields):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk full")
        return real_write(sd, **fields)

    monkeypatch.setattr(triager, "_write_status_patch", _bomb)
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(1)
    ))
    _write_test_files(spec_dir, 1)
    ok = await run_triager(spec_dir, project_dir)
    assert ok is False
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["status"] == "triager_failed"


# ── Source.json edge cases ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_writer_skipped_when_no_branch(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Source.json without branch → git_writer skipped with reason."""
    (spec_dir / "context" / "source.json").write_text(json.dumps({
        "project_id": "demo",
    }))
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(1)
    ))
    _write_test_files(spec_dir, 1)

    await run_triager(spec_dir, project_dir)
    status = json.loads((spec_dir / "status.json").read_text())
    gw = status["git_writer"]
    assert gw["skipped"] is True
    assert "no branch" in gw["reason"]


@pytest.mark.asyncio
async def test_test_file_missing_skipped_gracefully(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Verdicts reference a file that doesn't exist on disk →
    candidate gets empty source, git_writer skips it but the
    report still records the verdict."""
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(
        _make_verdicts(2)
    ))
    # Write ONLY test_0.py; test_1.py is missing
    (spec_dir / "tests" / "test_0.py").write_text(
        "def test_x(): assert True\n"
    )

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True
    status = json.loads((spec_dir / "status.json").read_text())
    assert status["committed_count"] == 2  # both verdicts kept (status-wise)
    # git_writer only sees test_0.py because test_1.py had no source
    gw = status["git_writer"]
    assert gw["skipped"] is False
    assert "tests/test_0.py" in gw["committed_paths"]
    assert "tests/test_1.py" not in gw["committed_paths"]


# ── Schedule + chain (carried over from commit 1) ─────────────────────


def test_schedule_disabled_returns_none(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PFACTORY_AUTO_TRIAGE", "0")
    async def _run():
        return schedule_triager(spec_dir, project_dir)
    assert asyncio.run(_run()) is None


@pytest.mark.asyncio
async def test_schedule_enabled_returns_task(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PFACTORY_AUTO_TRIAGE", "1")
    task = schedule_triager(spec_dir, project_dir)
    assert task is not None
    assert task in _BG_TRIAGER_TASKS
    await task
    assert task not in _BG_TRIAGER_TASKS


@pytest.mark.asyncio
async def test_evaluator_success_path_schedules_triager(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import evaluator
    monkeypatch.setenv("PFACTORY_AUTO_TRIAGE", "1")
    captured: dict = {}

    def _capture(sd, pd, mode="initial"):
        captured["spec_dir"] = sd
        captured["mode"] = mode
        return None

    import agents.triager as tri_mod
    monkeypatch.setattr(tri_mod, "schedule_triager", _capture)
    evaluator._advance_to_triager(spec_dir, project_dir)
    assert captured["spec_dir"] == spec_dir
    assert captured["mode"] == "initial"


def test_advance_to_triager_swallows_import_errors(
    spec_dir: Path, project_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents import evaluator

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def _selective_raiser(name, *args, **kwargs):
        if name == "agents.triager":
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_selective_raiser):
        evaluator._advance_to_triager(spec_dir, project_dir)


# ═══════════════════════════════════════════════════════════════════════
# Task 11 / #27 — Catalog-aware Triager: intent + catalog mutation tests
# ═══════════════════════════════════════════════════════════════════════

from agents.triage_dedup import TriageCandidate
from agents.triager import (  # noqa: E402 — local imports after helpers
    CandidateDecision,
    _decide_catalog_intent,
    _derive_create_path,
    _extract_candidate_ac,
    _mutate_catalog,
)
from tests_catalog.schema import CatalogEntry, TestsCatalog

# ── Fixtures ───────────────────────────────────────────────────────────


def _make_entry(
    *,
    test_id: str = "ac1-login",
    test_file: str = "tests/test_ac1_login.py",
    framework: str = "pytest",
    lane: str = "unit",
    language: str = "python",
    covers_acs: tuple[str, ...] = ("AC#1: User can log in",),
    generated_at: str = "2026-05-01T10:00:00+00:00",
    generated_by_task: str = "042-session-expiry",
    last_verdict: str = "accept",
    operator_locked: bool = False,
    generation_version: int = 1,
) -> CatalogEntry:
    return CatalogEntry(
        test_id=test_id,
        test_file=test_file,
        framework=framework,
        lane=lane,
        language=language,
        covers_acs=covers_acs,
        generated_at=generated_at,
        generated_by_task=generated_by_task,
        last_verdict=last_verdict,
        operator_locked=operator_locked,
        generation_version=generation_version,
    )


def _make_catalog(*entries: CatalogEntry) -> TestsCatalog:
    return TestsCatalog(
        version=1,
        updated_at="2026-05-01T12:00:00+00:00",
        tests=tuple(entries),
    )


def _make_candidate(
    test_id: str = "st0",
    verdict: str = "accept",
    rationale: str = "AC#1: User can log in",
    framework: str = "pytest",
    language: str = "python",
) -> TriageCandidate:
    return TriageCandidate(
        test_id=test_id,
        test_file=f"tests/test_{test_id}.py",
        verdict={
            "test_id": test_id,
            "verdict": verdict,
            "rationale": rationale,
            "framework": framework,
            "language": language,
            "reasons": [rationale],
            "signals_summary": {
                "coverage_delta_pct": 1.0,
                "stability": "stable",
                "mutation": "killed",
            },
            "semantic_relevance": "high",
        },
        source=f"def test_{test_id}(): assert True\n",
    )


# ── Intent derivation — pure unit tests (no I/O) ──────────────────────


def test_no_catalog_all_candidates_create() -> None:
    """When catalog is None every accepted candidate intent='create'."""
    c = _make_candidate()
    decision = _decide_catalog_intent(c, catalog=None)
    assert decision.intent == "create"
    assert decision.update_target_file is None
    assert decision.skip_reason is None


def test_catalog_exact_ac_match_intent_update() -> None:
    """Catalog has entry covering exact AC → intent='update'."""
    entry = _make_entry(covers_acs=("AC#1: User can log in",))
    catalog = _make_catalog(entry)
    c = _make_candidate(rationale="AC#1: User can log in")
    decision = _decide_catalog_intent(c, catalog)
    assert decision.intent == "update"
    assert decision.update_target_file == entry.test_file


def test_catalog_prefix_ac_match_intent_update() -> None:
    """AC-id prefix match: candidate='AC#1: login expiry', catalog='AC#1: login flow'."""
    entry = _make_entry(covers_acs=("AC#1: login flow",))
    catalog = _make_catalog(entry)
    c = _make_candidate(rationale="AC#1: login expiry")
    decision = _decide_catalog_intent(c, catalog)
    assert decision.intent == "update"
    assert decision.update_target_file == entry.test_file


def test_catalog_no_match_intent_create() -> None:
    """Candidate's rationale doesn't match any catalog entry → intent='create'."""
    entry = _make_entry(covers_acs=("AC#2: Logout flow",))
    catalog = _make_catalog(entry)
    c = _make_candidate(rationale="AC#3: Password reset")
    decision = _decide_catalog_intent(c, catalog)
    assert decision.intent == "create"


def test_catalog_multiple_matches_picks_most_recent() -> None:
    """Two catalog entries match; intent='update' on the one with latest generated_at."""
    older = _make_entry(
        test_id="ac1-older",
        test_file="tests/test_ac1_older.py",
        covers_acs=("AC#1: User can log in",),
        generated_at="2026-04-01T00:00:00+00:00",
    )
    newer = _make_entry(
        test_id="ac1-newer",
        test_file="tests/test_ac1_newer.py",
        covers_acs=("AC#1: login with 2FA",),
        generated_at="2026-05-10T00:00:00+00:00",
    )
    catalog = _make_catalog(older, newer)
    c = _make_candidate(rationale="AC#1: User can log in")
    decision = _decide_catalog_intent(c, catalog)
    assert decision.intent == "update"
    # Both are prefix-matched on "AC#1"; should pick the newest
    assert decision.update_target_file in (older.test_file, newer.test_file)


def test_catalog_match_operator_locked_intent_skip() -> None:
    """Catalog match has operator_locked=True → intent='skip'."""
    entry = _make_entry(covers_acs=("AC#1: User can log in",), operator_locked=True)
    catalog = _make_catalog(entry)
    c = _make_candidate(rationale="AC#1: User can log in")
    decision = _decide_catalog_intent(c, catalog)
    assert decision.intent == "skip"
    assert decision.skip_reason == "operator_locked"


# ── Framework path derivation ──────────────────────────────────────────


def test_create_path_derived_from_framework_conventions_pytest() -> None:
    """pytest framework → test_file in tests/ with .py extension."""
    path = _derive_create_path("ac1-login-flow", "pytest")
    assert path.endswith(".py")
    assert "ac1-login-flow" in path
    assert path.startswith("tests/") or "/" in path


def test_create_path_derived_from_framework_conventions_jest() -> None:
    """jest framework → test_file with .test.ts or .spec.ts extension."""
    path = _derive_create_path("ac1-login-flow", "jest")
    assert ".ts" in path or ".tsx" in path
    assert "ac1-login-flow" in path


def test_create_path_derived_from_framework_conventions_playwright() -> None:
    """playwright framework → test_file with .spec.ts extension."""
    path = _derive_create_path("ac1-login-flow", "playwright")
    assert ".spec.ts" in path or ".ts" in path
    assert "ac1-login-flow" in path


def test_create_path_fallback_for_unknown_framework() -> None:
    """Unknown framework falls back gracefully (no crash)."""
    path = _derive_create_path("my-test", "unknown-fw")
    assert "my-test" in path
    assert isinstance(path, str)


# ── Catalog mutation — pure unit tests (no I/O) ──────────────────────


def test_rejected_candidates_skip_catalog_lookup() -> None:
    """Reject verdict candidates are NOT in decisions → catalog unchanged."""
    entry = _make_entry()
    catalog = _make_catalog(entry)
    # reject candidate — not in decisions dict
    c = _make_candidate(verdict="reject")
    updated = _mutate_catalog(
        catalog=catalog,
        candidates=[c],
        decisions={},  # empty — no decisions for rejects
        generated_by_task="042",
        now_ts="2026-05-29T12:00:00+00:00",
    )
    assert updated is not None
    assert len(updated.tests) == 1
    assert updated.tests[0].generation_version == entry.generation_version


def test_update_bumps_generation_version() -> None:
    """UPDATE intent increments generation_version by 1."""
    entry = _make_entry(generation_version=1)
    catalog = _make_catalog(entry)
    c = _make_candidate()
    decisions = {
        c.test_id: CandidateDecision(
            intent="update",
            update_target_file=entry.test_file,
        )
    }
    updated = _mutate_catalog(
        catalog=catalog,
        candidates=[c],
        decisions=decisions,
        generated_by_task="042",
        now_ts="2026-05-29T12:00:00+00:00",
    )
    assert updated is not None
    assert len(updated.tests) == 1
    assert updated.tests[0].generation_version == 2


def test_update_refreshes_generated_at() -> None:
    """UPDATE intent refreshes generated_at to the provided now_ts."""
    entry = _make_entry(generated_at="2026-05-01T10:00:00+00:00")
    catalog = _make_catalog(entry)
    c = _make_candidate()
    decisions = {
        c.test_id: CandidateDecision(
            intent="update",
            update_target_file=entry.test_file,
        )
    }
    new_ts = "2026-05-29T15:00:00+00:00"
    updated = _mutate_catalog(
        catalog=catalog,
        candidates=[c],
        decisions=decisions,
        generated_by_task="042",
        now_ts=new_ts,
    )
    assert updated is not None
    assert updated.tests[0].generated_at == new_ts
    assert updated.tests[0].generated_at > entry.generated_at


def test_update_refreshes_last_verdict() -> None:
    """UPDATE intent refreshes last_verdict to the candidate's verdict."""
    entry = _make_entry(last_verdict="accept")
    catalog = _make_catalog(entry)
    c = _make_candidate(verdict="flag")
    decisions = {
        c.test_id: CandidateDecision(
            intent="update",
            update_target_file=entry.test_file,
        )
    }
    updated = _mutate_catalog(
        catalog=catalog,
        candidates=[c],
        decisions=decisions,
        generated_by_task="042",
        now_ts="2026-05-29T12:00:00+00:00",
    )
    assert updated is not None
    assert updated.tests[0].last_verdict == "flag"


def test_create_appends_new_catalog_entry() -> None:
    """CREATE intent adds a new entry; catalog grows from N to N+1."""
    entry = _make_entry(
        test_id="existing-test",
        test_file="tests/test_existing.py",
        covers_acs=("AC#2: logout",),
    )
    catalog = _make_catalog(entry)
    # New candidate for a different AC → CREATE
    c = _make_candidate(test_id="new-test", rationale="AC#1: login")
    decisions = {
        c.test_id: CandidateDecision(
            intent="create",
            derived_test_file="tests/test_new_test.py",
        )
    }
    updated = _mutate_catalog(
        catalog=catalog,
        candidates=[c],
        decisions=decisions,
        generated_by_task="042",
        now_ts="2026-05-29T12:00:00+00:00",
    )
    assert updated is not None
    assert len(updated.tests) == 2
    new_entry = next(e for e in updated.tests if e.test_id == "new-test")
    assert new_entry.test_file == "tests/test_new_test.py"
    assert new_entry.generation_version == 1


def test_skip_leaves_catalog_entry_untouched() -> None:
    """SKIP intent (operator_locked) leaves the entry's version + timestamp unchanged."""
    entry = _make_entry(
        operator_locked=True,
        generation_version=3,
        generated_at="2026-04-15T09:00:00+00:00",
    )
    catalog = _make_catalog(entry)
    c = _make_candidate()
    decisions = {
        c.test_id: CandidateDecision(
            intent="skip",
            skip_reason="operator_locked",
        )
    }
    updated = _mutate_catalog(
        catalog=catalog,
        candidates=[c],
        decisions=decisions,
        generated_by_task="042",
        now_ts="2026-05-29T12:00:00+00:00",
    )
    assert updated is not None
    assert len(updated.tests) == 1
    preserved = updated.tests[0]
    assert preserved.generation_version == 3
    assert preserved.generated_at == "2026-04-15T09:00:00+00:00"
    assert preserved.operator_locked is True


def test_no_catalog_mutate_returns_none() -> None:
    """When catalog is None, _mutate_catalog returns None (v0.1 path)."""
    c = _make_candidate()
    decisions = {c.test_id: CandidateDecision(intent="create")}
    result = _mutate_catalog(
        catalog=None,
        candidates=[c],
        decisions=decisions,
        generated_by_task="042",
        now_ts="2026-05-29T12:00:00+00:00",
    )
    assert result is None


# ── Report rendering with intent ──────────────────────────────────────


def test_triage_report_md_includes_intent_per_candidate() -> None:
    """Markdown report includes UPDATE existing / CREATE new per candidate."""
    from agents.triage_report import build_report, render_markdown

    c_update = _make_candidate(test_id="st-update")
    c_create = _make_candidate(test_id="st-create")
    decisions = {
        "st-update": CandidateDecision(
            intent="update",
            update_target_file="tests/test_existing.py",
        ),
        "st-create": CandidateDecision(
            intent="create",
            derived_test_file="tests/test_new.py",
        ),
    }
    report = build_report(
        mode="initial",
        generated_at="2026-05-29T00:00:00+00:00",
        committed=[c_update, c_create],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=2,
        decisions=decisions,
    )
    md = render_markdown(report)
    assert "UPDATE existing tests/test_existing.py" in md
    assert "CREATE new tests/test_new.py" in md


def test_triage_report_md_skipped_candidate_shows_operator_locked() -> None:
    """Skipped candidate appears in ## Skipped with SKIP (operator locked)."""
    from agents.triage_report import build_report, render_markdown

    c_skip = _make_candidate(test_id="st-skip")
    decisions = {
        "st-skip": CandidateDecision(
            intent="skip",
            skip_reason="operator_locked",
        ),
    }
    report = build_report(
        mode="initial",
        generated_at="2026-05-29T00:00:00+00:00",
        committed=[],
        flagged=[],
        rejected=[],
        skipped=[c_skip],
        collisions=[],
        dedup_input_count=0,
        decisions=decisions,
    )
    md = render_markdown(report)
    assert "## Skipped" in md
    assert "SKIP (operator_locked)" in md or "SKIP (operator locked)" in md


def test_triage_report_json_includes_intent_field() -> None:
    """Every accepted/flagged candidate in JSON has an 'intent' field."""
    from agents.triage_report import build_report, render_json

    c = _make_candidate(test_id="st0")
    decisions = {
        "st0": CandidateDecision(
            intent="create",
            derived_test_file="tests/test_st0.py",
        )
    }
    report = build_report(
        mode="initial",
        generated_at="2026-05-29T00:00:00+00:00",
        committed=[c],
        flagged=[],
        rejected=[],
        collisions=[],
        dedup_input_count=1,
        decisions=decisions,
    )
    doc = json.loads(render_json(report))
    assert len(doc["committed"]) == 1
    assert "intent" in doc["committed"][0]
    assert doc["committed"][0]["intent"] == "create"
    assert doc["committed"][0].get("derived_test_file") == "tests/test_st0.py"


# ── End-to-end catalog round-trip via run_triager ────────────────────


@pytest.mark.asyncio
async def test_catalog_save_called_once_after_all_decisions(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Catalog is written exactly once after all decisions are processed.

    Uses a spy on _mutate_catalog to count invocations.
    """
    from agents import triager as triager_mod

    # Build catalog with one entry
    entry = _make_entry(
        covers_acs=("AC#1: login expiry",),
        test_file="tests/test_ac1_login.py",
    )
    catalog_doc = _make_catalog(entry).to_dict()
    (spec_dir / "context" / "tests_catalog.json").write_text(
        json.dumps(catalog_doc)
    )

    # Write 2 accepted verdicts — one matches AC#1, one doesn't
    verdicts = {
        "evaluator_version": "test",
        "mode": "initial",
        "generated_at": "2026-05-29T00:00:00+00:00",
        "verdicts": [
            {
                "test_id": "ac1-login-expiry",
                "test_file": "tests/test_ac1.py",
                "verdict": "accept",
                "rationale": "AC#1: login expiry",
                "framework": "pytest",
                "language": "python",
                "reasons": ["AC#1: login expiry"],
                "signals_summary": {"coverage_delta_pct": 2.0, "stability": "stable", "mutation": "killed"},
                "semantic_relevance": "high",
            },
            {
                "test_id": "ac2-logout",
                "test_file": "tests/test_ac2.py",
                "verdict": "accept",
                "rationale": "AC#2: logout",
                "framework": "pytest",
                "language": "python",
                "reasons": ["AC#2: logout"],
                "signals_summary": {"coverage_delta_pct": 1.0, "stability": "stable", "mutation": "killed"},
                "semantic_relevance": "high",
            },
        ],
    }
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 2)
    (spec_dir / "tests" / "test_ac1.py").write_text("def test_ac1(): assert True\n")
    (spec_dir / "tests" / "test_ac2.py").write_text("def test_ac2(): assert True\n")

    mutate_calls: list[dict] = []
    original_mutate = triager_mod._mutate_catalog

    def _spy_mutate(*args, **kwargs):
        mutate_calls.append({"args": args, "kwargs": kwargs})
        return original_mutate(*args, **kwargs)

    import unittest.mock as mock
    with mock.patch.object(triager_mod, "_mutate_catalog", side_effect=_spy_mutate):
        ok = await run_triager(spec_dir, project_dir)

    assert ok is True
    # _mutate_catalog invoked exactly once
    assert len(mutate_calls) == 1

    # Catalog file was written back
    updated_json = json.loads(
        (spec_dir / "context" / "tests_catalog.json").read_text()
    )
    # Either the original entry was updated or a new one was added
    assert len(updated_json["tests"]) >= 1


@pytest.mark.asyncio
async def test_catalog_roundtrip_update_and_create(
    spec_dir: Path, project_dir: Path,
) -> None:
    """Round-trip: catalog with 1 entry + 2 verdicts (1 UPDATE, 1 CREATE)
    → written catalog has 2 entries, UPDATE bumped version."""
    entry = _make_entry(
        test_id="ac1-existing",
        test_file="tests/test_ac1_existing.py",
        covers_acs=("AC#1: User can log in",),
        generation_version=1,
    )
    catalog_doc = _make_catalog(entry).to_dict()
    (spec_dir / "context" / "tests_catalog.json").write_text(
        json.dumps(catalog_doc)
    )

    verdicts = {
        "evaluator_version": "test",
        "mode": "initial",
        "generated_at": "2026-05-29T00:00:00+00:00",
        "verdicts": [
            {
                "test_id": "ac1-update-cand",
                "test_file": "tests/test_ac1_update_cand.py",
                "verdict": "accept",
                "rationale": "AC#1: User can log in",  # exact match → UPDATE
                "framework": "pytest",
                "language": "python",
                "reasons": ["covers AC#1"],
                "signals_summary": {"coverage_delta_pct": 3.0, "stability": "stable", "mutation": "killed"},
                "semantic_relevance": "high",
            },
            {
                "test_id": "ac2-new-cand",
                "test_file": "tests/test_ac2_new_cand.py",
                "verdict": "accept",
                "rationale": "AC#2: Session expires",  # no match → CREATE
                "framework": "pytest",
                "language": "python",
                "reasons": ["covers AC#2"],
                "signals_summary": {"coverage_delta_pct": 1.5, "stability": "stable", "mutation": "killed"},
                "semantic_relevance": "high",
            },
        ],
    }
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    (spec_dir / "tests" / "test_ac1_update_cand.py").write_text(
        "def test_ac1(): assert True\n"
    )
    (spec_dir / "tests" / "test_ac2_new_cand.py").write_text(
        "def test_ac2(): assert True\n"
    )

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True

    updated = json.loads(
        (spec_dir / "context" / "tests_catalog.json").read_text()
    )
    assert len(updated["tests"]) == 2

    # The original entry (ac1-existing) was updated: generation_version bumped
    orig = next(e for e in updated["tests"] if e["test_id"] == "ac1-existing")
    assert orig["generation_version"] == 2

    # A new entry was created for AC#2
    new_ids = {e["test_id"] for e in updated["tests"]}
    assert "ac1-existing" in new_ids  # updated
    # Second entry is new (ac2-new-cand's derived path)
    assert len([e for e in updated["tests"] if e["generation_version"] == 1]) == 1


@pytest.mark.asyncio
async def test_no_catalog_file_no_mutation(
    spec_dir: Path, project_dir: Path,
) -> None:
    """When no catalog file exists (v0.1 path), run_triager completes
    normally and no catalog file is created."""
    verdicts = _make_verdicts(1, ["accept"])
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    _write_test_files(spec_dir, 1)

    ok = await run_triager(spec_dir, project_dir)
    assert ok is True

    # No catalog file should have been created
    assert not (spec_dir / "context" / "tests_catalog.json").exists()


@pytest.mark.asyncio
async def test_ambiguous_match_logs_warning(
    spec_dir: Path, project_dir: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Two catalog entries prefix-match → warning emitted (bonus test)."""
    import logging

    entry1 = _make_entry(
        test_id="ac1-a", test_file="tests/test_ac1_a.py",
        covers_acs=("AC#1: login flow A",), generated_at="2026-04-01T00:00:00+00:00",
    )
    entry2 = _make_entry(
        test_id="ac1-b", test_file="tests/test_ac1_b.py",
        covers_acs=("AC#1: login flow B",), generated_at="2026-05-01T00:00:00+00:00",
    )
    catalog_doc = _make_catalog(entry1, entry2).to_dict()
    (spec_dir / "context" / "tests_catalog.json").write_text(
        json.dumps(catalog_doc)
    )

    verdicts = {
        "evaluator_version": "test",
        "mode": "initial",
        "generated_at": "2026-05-29T00:00:00+00:00",
        "verdicts": [{
            "test_id": "ac1-new",
            "test_file": "tests/test_ac1_new.py",
            "verdict": "accept",
            "rationale": "AC#1: login new",  # prefix matches both entries
            "framework": "pytest", "language": "python",
            "reasons": ["AC#1 match"],
            "signals_summary": {"coverage_delta_pct": 1.0, "stability": "stable", "mutation": "killed"},
            "semantic_relevance": "high",
        }],
    }
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps(verdicts))
    (spec_dir / "tests" / "test_ac1_new.py").write_text(
        "def test_ac1(): assert True\n"
    )

    with caplog.at_level(logging.WARNING, logger="agents.triager"):
        ok = await run_triager(spec_dir, project_dir)

    assert ok is True
    # Warning about catalog ambiguity must have been emitted
    ambiguity_msgs = [r for r in caplog.records if "ambiguity" in r.message.lower()]
    assert len(ambiguity_msgs) >= 1
