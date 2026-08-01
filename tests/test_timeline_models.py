#!/usr/bin/env python3
"""
Tests for TaskFileView.from_dict status default (Factory #431).

A default that asserts a task is still "active" when its true status is
unknown is the bug this epic targets: a merged/abandoned task whose
persisted record is missing (or has lost) its `status` field would silently
look alive again, so `get_active_tasks()` / `get_pending_tasks_for_file()`
keep warning other tasks about a conflict that no longer exists, and
`add_main_event()` keeps incrementing its drift counter forever. Picking
another *specific* outcome (e.g. "abandoned") instead of "active" is the same
bug shape pointed at a different value -- the honest read is "unknown".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from merge.timeline_models import BranchPoint, FileTimeline, TaskFileView


def _base_data(**overrides: object) -> dict:
    data = {
        "task_id": "task-1",
        "branch_point": BranchPoint(
            commit_hash="abc123",
            content="hello",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        ).to_dict(),
    }
    data.update(overrides)
    return data


def test_from_dict_preserves_explicit_status():
    """Sanity: an explicit status round-trips unchanged."""
    view = TaskFileView.from_dict(_base_data(status="merged"))
    assert view.status == "merged"


def test_from_dict_missing_status_reads_as_unknown_not_active(caplog):
    """The regression: a record with no `status` must NOT default to active.

    `to_dict()` always writes `status`, so a missing key means the persisted
    record predates that field or was corrupted -- the true lifecycle state
    is unknown. "abandoned" would be just as dishonest as "active" in the
    other direction: it asserts someone gave up on this task, which we don't
    know either. "unknown" is the honest read, and it is still correctly
    excluded from active-task queries instead of silently reappearing as
    in-progress work.
    """
    with caplog.at_level(logging.WARNING):
        view = TaskFileView.from_dict(_base_data())

    assert view.status == "unknown"
    assert view.status != "active"
    assert view.status != "abandoned"
    assert any("task-1" in record.message for record in caplog.records)


def test_missing_status_task_excluded_from_active_tasks():
    """End-to-end: the corrupted record does not resurrect a stale merge-conflict warning."""
    timeline = FileTimeline(file_path="src/thing.py")
    timeline.add_task_view(TaskFileView.from_dict(_base_data()))

    assert timeline.get_active_tasks() == []
