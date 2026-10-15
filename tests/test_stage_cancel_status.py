"""A cancelled stage task must leave a ``<stage>_failed`` status (#860)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from agents import evaluator, gen_functional, planner

if TYPE_CHECKING:
    from pathlib import Path


def _spec(tmp_path: Path, status: str, **extra: object) -> Path:
    (tmp_path / "status.json").write_text(json.dumps({"status": status, **extra}))
    return tmp_path


def _status(spec_dir: Path) -> dict:
    return json.loads((spec_dir / "status.json").read_text())


async def _hang(*_a: object, **_k: object) -> None:
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_planner_cancelled_mid_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_AUTO_PLAN", "1")
    monkeypatch.setattr(planner, "run_planner", _hang)
    spec_dir = _spec(tmp_path, "planning")
    task = planner.schedule_planner(spec_dir, tmp_path)
    await asyncio.sleep(0)  # let the stub start
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)  # done-callbacks run on the next loop tick
    status = _status(spec_dir)
    assert status["status"] == "planner_failed"
    assert status["phase"] == "planner_cancelled"
    assert status["planner_error"] == "task cancelled"


@pytest.mark.asyncio
async def test_gen_functional_cancelled_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PFACTORY_AUTO_GENERATE", "1")
    monkeypatch.setattr(gen_functional, "run_gen_functional", _hang)
    spec_dir = _spec(tmp_path, "planned")
    task = gen_functional.schedule_gen_functional(spec_dir, tmp_path)
    task.cancel()  # never got a turn on the loop
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert _status(spec_dir)["status"] == "gen_functional_failed"


@pytest.mark.asyncio
async def test_evaluator_already_failed_status_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PFACTORY_AUTO_EVALUATE", "1")
    monkeypatch.setattr(evaluator, "run_evaluator", _hang)
    spec_dir = _spec(tmp_path, "evaluator_failed", evaluator_error="boom")
    task = evaluator.schedule_evaluator(spec_dir, tmp_path)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    status = _status(spec_dir)
    assert status["status"] == "evaluator_failed"
    assert status["evaluator_error"] == "boom"
    assert "phase" not in status
