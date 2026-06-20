"""Tests for the shared per-agent infrastructure (factory-agents dedup, #195).

These cover the primitives extracted from planner.py / gen_functional.py /
triager.py / stage_events.py: now_iso, truthy, read_status,
write_status_patch, iter_subtasks, and the BackgroundTaskRegistry GC anchor.
The extraction is behaviour-preserving, so the assertions mirror the
behaviour the per-module copies previously had.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from agents.agent_infra import (
    BackgroundTaskRegistry,
    iter_subtasks,
    now_iso,
    read_status,
    truthy,
    write_status_patch,
)

# ── now_iso ─────────────────────────────────────────────────────────────────


def test_now_iso_is_tz_aware_seconds_resolution() -> None:
    stamp = now_iso()
    assert stamp.endswith("+00:00")
    # seconds resolution → no fractional-seconds component
    assert "." not in stamp


# ── truthy ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", " on ", "On"])
def test_truthy_accepts_canonical_true_values(value: str) -> None:
    assert truthy(value) is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "maybe"])
def test_truthy_rejects_everything_else(value: str | None) -> None:
    assert truthy(value) is False


# ── read_status ─────────────────────────────────────────────────────────────


def test_read_status_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_status(tmp_path) == {}


def test_read_status_corrupt_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text("{not json")
    assert read_status(tmp_path) == {}


def test_read_status_reads_existing(tmp_path: Path) -> None:
    (tmp_path / "status.json").write_text(json.dumps({"status": "planned"}))
    assert read_status(tmp_path) == {"status": "planned"}


# ── write_status_patch ──────────────────────────────────────────────────────


def test_write_status_patch_merges_and_stamps(tmp_path: Path) -> None:
    write_status_patch(tmp_path, "planner", status="planning", phase="started")
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["status"] == "planning"
    assert data["phase"] == "started"
    assert "updated_at" in data


def test_write_status_patch_preserves_prior_fields(tmp_path: Path) -> None:
    write_status_patch(tmp_path, "planner", status="planning", task_id="t-1")
    write_status_patch(tmp_path, "planner", status="planned")
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["status"] == "planned"
    assert data["task_id"] == "t-1"


def test_write_status_patch_emits_stage_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Path, dict[str, object], str]] = []

    def _fake_emit(spec_dir: Path, status: dict[str, object], *, stage: str) -> None:
        captured.append((spec_dir, status, stage))

    monkeypatch.setattr("agents.stage_events.emit_stage_event", _fake_emit)
    write_status_patch(tmp_path, "gen_functional", status="generated")
    assert len(captured) == 1
    assert captured[0][2] == "gen_functional"
    assert captured[0][1]["status"] == "generated"


# ── iter_subtasks ───────────────────────────────────────────────────────────


@dataclass
class _Subtask:
    id: str
    status: str = "pending"


@dataclass
class _Phase:
    subtasks: list[_Subtask] = field(default_factory=list)


@dataclass
class _Plan:
    phases: list[_Phase] = field(default_factory=list)


def _two_phase_plan() -> _Plan:
    return _Plan(
        phases=[
            _Phase(subtasks=[_Subtask("a", "pending"), _Subtask("b", "done")]),
            _Phase(subtasks=[_Subtask("c", "pending")]),
        ]
    )


def test_iter_subtasks_yields_all_pairs_in_order() -> None:
    plan = _two_phase_plan()
    ids = [st.id for _phase, st in iter_subtasks(plan)]
    assert ids == ["a", "b", "c"]


def test_iter_subtasks_pairs_phase_with_subtask() -> None:
    plan = _two_phase_plan()
    for phase, subtask in iter_subtasks(plan):
        assert subtask in phase.subtasks


def test_iter_subtasks_predicate_filters() -> None:
    plan = _two_phase_plan()
    ids = [st.id for _phase, st in iter_subtasks(plan, lambda st: st.status == "pending")]
    assert ids == ["a", "c"]


def test_iter_subtasks_empty_plan() -> None:
    assert list(iter_subtasks(_Plan())) == []


# ── BackgroundTaskRegistry ──────────────────────────────────────────────────


def test_registry_starts_empty() -> None:
    reg = BackgroundTaskRegistry()
    assert reg.tasks == set()


@pytest.mark.asyncio
async def test_registry_anchors_then_discards() -> None:
    reg = BackgroundTaskRegistry()

    async def _work() -> str:
        return "ok"

    task = reg.schedule(_work())
    # Anchored while in flight.
    assert task in reg.tasks
    result = await task
    assert result == "ok"
    # done_callback runs on the event loop; yield once so it fires.
    await asyncio.sleep(0)
    assert task not in reg.tasks


@pytest.mark.asyncio
async def test_registry_tasks_set_identity_is_stable() -> None:
    reg = BackgroundTaskRegistry()
    alias = reg.tasks

    async def _work() -> None:
        return None

    reg.schedule(_work())
    # An external alias (mirrors module-level _BG_*_TASKS) sees the same set.
    assert alias is reg.tasks
    assert len(alias) == 1
