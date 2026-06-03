"""Tests for the liveness sweep driver (#95)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from agents.liveness_sweep import (
    default_workspace_root,
    iter_spec_dirs,
    main,
    sweep,
)

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _spec(root: Path, project: str, spec: str, **status: object) -> Path:
    d = root / "workspaces" / project / "specs" / spec
    d.mkdir(parents=True, exist_ok=True)
    if status:
        (d / "status.json").write_text(json.dumps(status))
    return d


# ── iter_spec_dirs ──────────────────────────────────────────────────────────


def test_iter_finds_only_dirs_with_status(tmp_path: Path) -> None:
    a = _spec(tmp_path, "p1", "specA", status="generating", updated_at=_iso(_NOW))
    b = _spec(tmp_path, "p1", "specB", status="triaging", updated_at=_iso(_NOW))
    _spec(tmp_path, "p1", "specC")  # no status.json → skipped
    found = set(iter_spec_dirs(tmp_path))
    assert found == {a, b}


def test_iter_missing_tree_is_empty(tmp_path: Path) -> None:
    assert list(iter_spec_dirs(tmp_path / "nope")) == []


# ── sweep ───────────────────────────────────────────────────────────────────


def test_sweep_flips_stalled_and_leaves_others(tmp_path: Path) -> None:
    stale = _spec(
        tmp_path, "p1", "stalledSpec",
        status="generating", updated_at=_iso(_NOW - timedelta(seconds=3600)),
    )
    fresh = _spec(
        tmp_path, "p1", "freshSpec",
        status="triaging", updated_at=_iso(_NOW - timedelta(seconds=10)),
    )
    settled = _spec(
        tmp_path, "p2", "doneSpec",
        status="triaged", updated_at=_iso(_NOW - timedelta(days=1)),
    )

    results = sweep(tmp_path, now=_NOW, deadline_seconds=900)
    by_dir = {d: v for d, v in results}

    assert by_dir[stale].stalled is True
    assert by_dir[fresh].stalled is False
    assert by_dir[settled].stalled is False  # terminal → never flipped

    assert json.loads((stale / "status.json").read_text())["status"] == "stalled"
    assert json.loads((fresh / "status.json").read_text())["status"] == "triaging"
    assert json.loads((settled / "status.json").read_text())["status"] == "triaged"


def test_sweep_empty_workspace_returns_empty(tmp_path: Path) -> None:
    assert sweep(tmp_path, now=_NOW) == []


def test_sweep_uses_env_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _spec(
        tmp_path, "p1", "s",
        status="evaluating", updated_at=_iso(_NOW - timedelta(seconds=5000)),
    )
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path))
    assert default_workspace_root() == tmp_path
    # No explicit root → resolves from env.
    results = sweep(now=_NOW, deadline_seconds=900)
    assert len(results) == 1 and results[0][1].stalled is True


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_main_reports_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # main() uses real wall-clock (no now injection), so pin updated_at far in
    # the past — always older than any deadline regardless of when CI runs.
    _spec(
        tmp_path, "p1", "s",
        status="planning", updated_at="2020-01-01T00:00:00+00:00",
    )
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path))
    rc = main(["--deadline", "900"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "STALLED" in out
    assert "flagged 1 stalled" in out
