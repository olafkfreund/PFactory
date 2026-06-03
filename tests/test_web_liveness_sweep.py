"""Tests for the web-server liveness sweep driver (#95).

Exercises the periodic-sweep glue without spawning the unbounded loop: the
single-iteration body is called directly, and the loop is started then
cancelled to prove it shuts down cleanly.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

# Add apps/web-server/ to sys.path so ``from server.background...`` resolves.
# When the web-server deps are absent (the backend-only test venv), conftest's
# pytest_ignore_collect skips this whole module; it runs under the web-server
# venv / CI where FastAPI is installed. Same pattern as test_pfactory_routes_tasks.
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
if str(WEB_SERVER_PATH) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_PATH))

from server.background.liveness_sweep import (  # noqa: E402
    liveness_sweep_loop,
    run_one_sweep,
)

# Far in the past → always older than any deadline regardless of wall-clock
# (run_one_sweep uses real `now`).
_STALE = "2020-01-01T00:00:00+00:00"


def _spec(root: Path, project: str, spec: str, **status: object) -> Path:
    d = root / "workspaces" / project / "specs" / spec
    d.mkdir(parents=True, exist_ok=True)
    if status:
        (d / "status.json").write_text(json.dumps(status))
    return d


def test_run_one_sweep_flags_stalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = _spec(tmp_path, "p1", "s1", status="generating", updated_at=_STALE)
    fresh = _spec(tmp_path, "p1", "s2", status="triaged", updated_at=_STALE)  # terminal
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path))

    results = run_one_sweep(deadline_seconds=900)
    by_dir = {d: v for d, v in results}

    assert by_dir[stale].stalled is True
    assert by_dir[fresh].stalled is False
    assert json.loads((stale / "status.json").read_text())["status"] == "stalled"
    assert json.loads((fresh / "status.json").read_text())["status"] == "triaged"


def test_run_one_sweep_empty_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path))
    assert run_one_sweep(deadline_seconds=900) == []


async def test_loop_runs_then_cancels_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path))
    task = asyncio.create_task(
        liveness_sweep_loop(interval_seconds=0.01, deadline_seconds=900)
    )
    await asyncio.sleep(0.03)  # let a couple of iterations run
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
