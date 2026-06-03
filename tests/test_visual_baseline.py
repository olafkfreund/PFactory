"""Tests for the visual-regression baseline store (#109).

Backend-pure: filesystem only (tmp_path), no image library or network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents.evidence.visual_baseline import (
    VisualBaselineError,
    accept_baseline,
    baseline_dir,
    baseline_path,
    baseline_status,
    has_baseline,
    list_baselines,
)

_PNG = b"\x89PNG\r\n\x1a\n fake image bytes"


# ── layout ───────────────────────────────────────────────────────────────────


def test_baseline_dir_is_under_findings(tmp_path: Path) -> None:
    d = baseline_dir(tmp_path, "checkout")
    assert d == tmp_path / "findings" / "visual_baselines" / "checkout"


def test_baseline_path_appends_snapshot(tmp_path: Path) -> None:
    p = baseline_path(tmp_path, "checkout", "summary.png")
    assert p == tmp_path / "findings" / "visual_baselines" / "checkout" / "summary.png"


# ── accept + has_baseline ────────────────────────────────────────────────────


def test_accept_from_bytes_then_has_baseline(tmp_path: Path) -> None:
    assert has_baseline(tmp_path, "web", "home.png") is False
    dest = accept_baseline(tmp_path, "web", "home.png", _PNG)
    assert dest.is_file()
    assert dest.read_bytes() == _PNG
    assert has_baseline(tmp_path, "web", "home.png") is True


def test_accept_from_path_copies_file(tmp_path: Path) -> None:
    captured = tmp_path / "captured.png"
    captured.write_bytes(_PNG)
    dest = accept_baseline(tmp_path, "web", "home.png", captured)
    assert dest.read_bytes() == _PNG
    # source is left in place (copy, not move)
    assert captured.is_file()


def test_accept_overwrites_existing_baseline(tmp_path: Path) -> None:
    accept_baseline(tmp_path, "web", "home.png", b"old")
    accept_baseline(tmp_path, "web", "home.png", b"new")
    assert baseline_path(tmp_path, "web", "home.png").read_bytes() == b"new"


def test_accept_missing_source_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        accept_baseline(tmp_path, "web", "home.png", tmp_path / "nope.png")


# ── safety (path traversal) ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "  ", "x\x00y"])
def test_unsafe_target_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(VisualBaselineError):
        baseline_dir(tmp_path, bad)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "sub/dir.png", ".."])
def test_unsafe_snapshot_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(VisualBaselineError):
        baseline_path(tmp_path, "web", bad)


# ── list + status ────────────────────────────────────────────────────────────


def test_list_baselines_empty_then_sorted(tmp_path: Path) -> None:
    assert list_baselines(tmp_path, "web") == []
    accept_baseline(tmp_path, "web", "b.png", _PNG)
    accept_baseline(tmp_path, "web", "a.png", _PNG)
    entries = list_baselines(tmp_path, "web")
    assert [e.snapshot for e in entries] == ["a.png", "b.png"]
    assert all(e.size_bytes == len(_PNG) for e in entries)


def test_baseline_status_classifies_new_vs_tracked(tmp_path: Path) -> None:
    accept_baseline(tmp_path, "web", "home.png", _PNG)
    status = baseline_status(tmp_path, "web", ["home.png", "pricing.png"])
    assert status == {"home.png": "tracked", "pricing.png": "new"}
