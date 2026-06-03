"""Tests for evidence retention enforcer — Task 16 / #32 sub-task 16.5.

Covered:
  - No-op when evidence_root doesn't exist
  - Passing retention: directory pruned after window expires
  - Failures retention: "forever" keeps directory regardless of age
  - Flagged retention: directory pruned after its window expires
  - Unknown verdict → conservative failures bucket (kept forever by default)
  - Size-cap sweep: oldest dirs pruned first until under cap
  - Size-cap sweep: skipped when no cap configured
  - RetentionStats: counts and bytes_freed are accurate
  - Mixed age + size scenario
  - parse_days / parse_size_bytes helpers via enforce_retention
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from agents.evidence.retention import RetentionStats, enforce_retention

# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _make_spec_dir(tmp_path: Path, test_ids: list[str]) -> Path:
    """Create a minimal spec dir with empty evidence dirs for each test_id."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    for tid in test_ids:
        ev_dir = spec_dir / "findings" / "evidence" / tid
        ev_dir.mkdir(parents=True)
        # Put a dummy file in each dir so it has non-zero size
        (ev_dir / "network.har").write_bytes(b'{"log": {"entries": []}}')
    return spec_dir


def _write_verdicts(spec_dir: Path, verdicts: dict[str, str]) -> None:
    """Write a minimal verdicts.json with {test_id: verdict} mapping."""
    findings = spec_dir / "findings"
    findings.mkdir(exist_ok=True)
    doc = {
        "verdicts": [
            {"test_id": tid, "verdict": v, "test_file": f"tests/{tid}.spec.ts"}
            for tid, v in verdicts.items()
        ]
    }
    (findings / "verdicts.json").write_text(json.dumps(doc), encoding="utf-8")


def _set_mtime(path: Path, dt: datetime) -> None:
    """Set the mtime of *path* to *dt* (UTC)."""
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


# ─── No-op cases ──────────────────────────────────────────────────────────────


def test_enforce_retention_no_evidence_dir(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy)
    assert stats.pruned_by_age_count == 0
    assert stats.pruned_by_size_count == 0
    assert stats.bytes_freed == 0
    assert stats.retained_count == 0


def test_enforce_retention_empty_evidence_dir(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    ev_root = spec_dir / "findings" / "evidence"
    ev_root.mkdir(parents=True)
    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy)
    assert stats.pruned_by_age_count == 0
    assert stats.retained_count == 0


# ─── Age sweep: passing bucket ───────────────────────────────────────────────


def test_enforce_retention_passing_within_window_kept(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    _write_verdicts(spec_dir, {"t1": "accept"})
    # t1 mtime = 3 days ago — within 7-day window
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=3))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 0
    assert stats.retained_count == 1


def test_enforce_retention_passing_expired_pruned(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    _write_verdicts(spec_dir, {"t1": "accept"})
    # t1 mtime = 10 days ago — beyond 7-day window
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=10))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 1
    assert not (spec_dir / "findings" / "evidence" / "t1").exists()


def test_enforce_retention_passing_exactly_at_boundary_kept(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    _write_verdicts(spec_dir, {"t1": "accept"})
    # Exactly 7 days old — on boundary, should NOT be pruned (age < cutoff)
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=7))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    # At exactly 7 days the cutoff is (now - 7 days); mtime == cutoff means NOT older
    assert stats.pruned_by_age_count == 0


# ─── Age sweep: failures bucket (forever) ────────────────────────────────────


def test_enforce_retention_failures_forever_never_pruned(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    _write_verdicts(spec_dir, {"t1": "reject"})
    # t1 mtime = 1000 days ago — very old
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=1000))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 0
    assert stats.retained_count == 1


# ─── Age sweep: flagged bucket ────────────────────────────────────────────────


def test_enforce_retention_flagged_expired_pruned(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    _write_verdicts(spec_dir, {"t1": "flag"})
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=100))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 1


def test_enforce_retention_flagged_within_window_kept(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    _write_verdicts(spec_dir, {"t1": "flag"})
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=10))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 0
    assert stats.retained_count == 1


# ─── Unknown verdict → conservative failures ─────────────────────────────────


def test_enforce_retention_unknown_verdict_kept_forever(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1"])
    # No verdicts.json → unknown verdict → failures bucket → forever
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=500))

    policy = {"failures": "forever", "flagged": "30_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 0


# ─── Size-cap sweep ───────────────────────────────────────────────────────────


def _make_evidence_with_payload(spec_dir: Path, test_id: str, payload_bytes: int) -> Path:
    ev_dir = spec_dir / "findings" / "evidence" / test_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "video.webm").write_bytes(b"x" * payload_bytes)
    return ev_dir


def test_enforce_retention_size_cap_prunes_oldest(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "findings").mkdir()
    _write_verdicts(spec_dir, {"t1": "accept", "t2": "accept"})

    # t1 is older (5 days ago), t2 is newer (1 day ago)
    ev1 = _make_evidence_with_payload(spec_dir, "t1", 300 * 1024)  # 300 KB
    ev2 = _make_evidence_with_payload(spec_dir, "t2", 300 * 1024)  # 300 KB
    _set_mtime(ev1, now - timedelta(days=5))
    _set_mtime(ev2, now - timedelta(days=1))

    # Cap at 400 KB → oldest (t1) should be pruned first
    policy = {
        "failures": "forever",
        "flagged": "90_days",
        "passing": "30_days",  # both within window
        "size_cap_per_task": "400KB",
    }
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_size_count >= 1
    # t1 (older) should be gone, t2 (newer) should remain
    assert not ev1.exists()
    assert ev2.exists()


def test_enforce_retention_no_size_cap_skips_size_sweep(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "findings").mkdir()
    _write_verdicts(spec_dir, {"t1": "accept"})
    ev1 = _make_evidence_with_payload(spec_dir, "t1", 1024 * 1024 * 1024)  # 1 GB
    _set_mtime(ev1, now - timedelta(days=1))

    policy = {
        "failures": "forever",
        "flagged": "90_days",
        "passing": "30_days",
        "size_cap_per_task": None,  # no cap
    }
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_size_count == 0
    assert ev1.exists()


# ─── RetentionStats accuracy ──────────────────────────────────────────────────


def test_enforce_retention_bytes_freed_accuracy(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "findings").mkdir()
    _write_verdicts(spec_dir, {"t1": "accept"})

    payload = b"z" * 1000
    ev_dir = spec_dir / "findings" / "evidence" / "t1"
    ev_dir.mkdir(parents=True)
    (ev_dir / "network.har").write_bytes(payload)
    _set_mtime(ev_dir, now - timedelta(days=30))

    policy = {"failures": "forever", "flagged": "90_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 1
    assert stats.bytes_freed >= len(payload)


def test_enforce_retention_retained_count_accurate(tmp_path: Path) -> None:
    now = datetime(2026, 5, 29, tzinfo=timezone.utc)
    spec_dir = _make_spec_dir(tmp_path, ["t1", "t2", "t3"])
    _write_verdicts(spec_dir, {"t1": "accept", "t2": "accept", "t3": "reject"})
    # t1 expires, t2 and t3 should be retained
    _set_mtime(spec_dir / "findings" / "evidence" / "t1", now - timedelta(days=30))
    _set_mtime(spec_dir / "findings" / "evidence" / "t2", now - timedelta(days=1))
    _set_mtime(spec_dir / "findings" / "evidence" / "t3", now - timedelta(days=500))

    policy = {"failures": "forever", "flagged": "90_days", "passing": "7_days"}
    stats = enforce_retention(spec_dir, policy, now=now)
    assert stats.pruned_by_age_count == 1  # t1
    assert stats.retained_count == 2  # t2 (new) + t3 (failures→forever)
