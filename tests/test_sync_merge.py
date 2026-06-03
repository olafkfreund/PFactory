#!/usr/bin/env python3
"""
Tests for test_plan.json sync merge logic
====================================================

Tests the subtask status regression prevention in worktree-to-main sync.
This tests the merge logic extracted from agent_service._sync_worktree_files().
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest


def merge_test_plan(main_plan: dict, worktree_plan: dict) -> dict:
    """
    Merge worktree implementation plan into main spec plan,
    preventing subtask status regressions.

    Extracted from agent_service._sync_worktree_files() for testability.
    """
    STATUS_ORDER = {"pending": 0, "in_progress": 1, "completed": 2, "failed": 2}

    # Preserve top-level fields from main spec
    preserved_status = main_plan.get("status")
    preserved_reason = main_plan.get("reviewReason")

    # Build map of main spec subtask statuses
    main_subtask_statuses = {}
    for phase in main_plan.get("phases", []):
        for subtask in phase.get("subtasks", []):
            sid = subtask.get("id")
            if sid:
                main_subtask_statuses[sid] = subtask.get("status", "pending")

    # Start from worktree plan (has latest structure)
    merged_plan = worktree_plan

    # Restore preserved top-level fields
    if preserved_status:
        merged_plan["status"] = preserved_status
    if preserved_reason:
        merged_plan["reviewReason"] = preserved_reason

    # Prevent subtask status regressions
    for phase in merged_plan.get("phases", []):
        for subtask in phase.get("subtasks", []):
            sid = subtask.get("id")
            if sid and sid in main_subtask_statuses:
                main_rank = STATUS_ORDER.get(main_subtask_statuses[sid], 0)
                wt_rank = STATUS_ORDER.get(subtask.get("status", "pending"), 0)
                if main_rank > wt_rank:
                    subtask["status"] = main_subtask_statuses[sid]

    return merged_plan


class TestImplementationPlanMerge:
    """Tests for implementation plan merge preventing status regressions."""

    def _make_plan(self, subtask_statuses: dict, status: str = None, review_reason: str = None) -> dict:
        """Helper to build a plan with given subtask statuses."""
        plan = {
            "phases": [
                {
                    "id": "phase-1",
                    "name": "Implementation",
                    "subtasks": [
                        {"id": sid, "description": f"Subtask {sid}", "status": st}
                        for sid, st in subtask_statuses.items()
                    ],
                }
            ]
        }
        if status:
            plan["status"] = status
        if review_reason:
            plan["reviewReason"] = review_reason
        return plan

    def test_prevents_completed_to_pending_regression(self):
        """Main spec has completed subtask; worktree has it as pending. Main wins."""
        main = self._make_plan({"st-1": "completed", "st-2": "pending"})
        worktree = self._make_plan({"st-1": "pending", "st-2": "in_progress"})

        merged = merge_test_plan(main, worktree)

        subtasks = {s["id"]: s["status"] for p in merged["phases"] for s in p["subtasks"]}
        assert subtasks["st-1"] == "completed"  # Regression prevented
        assert subtasks["st-2"] == "in_progress"  # Forward progress accepted

    def test_prevents_in_progress_to_pending_regression(self):
        """Main has in_progress; worktree has pending. Main wins."""
        main = self._make_plan({"st-1": "in_progress"})
        worktree = self._make_plan({"st-1": "pending"})

        merged = merge_test_plan(main, worktree)

        subtasks = {s["id"]: s["status"] for p in merged["phases"] for s in p["subtasks"]}
        assert subtasks["st-1"] == "in_progress"

    def test_allows_forward_progress(self):
        """Worktree has completed; main has pending. Worktree wins (forward progress)."""
        main = self._make_plan({"st-1": "pending"})
        worktree = self._make_plan({"st-1": "completed"})

        merged = merge_test_plan(main, worktree)

        subtasks = {s["id"]: s["status"] for p in merged["phases"] for s in p["subtasks"]}
        assert subtasks["st-1"] == "completed"

    def test_preserves_top_level_status(self):
        """Top-level status and reviewReason from main are preserved."""
        main = self._make_plan({"st-1": "completed"}, status="in_review", review_reason="Needs QA")
        worktree = self._make_plan({"st-1": "completed"}, status="building")

        merged = merge_test_plan(main, worktree)

        assert merged["status"] == "in_review"
        assert merged["reviewReason"] == "Needs QA"

    def test_new_subtask_in_worktree(self):
        """Subtask only in worktree (not in main) is kept as-is."""
        main = self._make_plan({"st-1": "completed"})
        worktree = self._make_plan({"st-1": "completed", "st-2": "pending"})

        merged = merge_test_plan(main, worktree)

        subtasks = {s["id"]: s["status"] for p in merged["phases"] for s in p["subtasks"]}
        assert subtasks["st-1"] == "completed"
        assert subtasks["st-2"] == "pending"

    def test_failed_status_not_regressed(self):
        """Failed status (rank 2) is not regressed to pending or in_progress."""
        main = self._make_plan({"st-1": "failed"})
        worktree = self._make_plan({"st-1": "pending"})

        merged = merge_test_plan(main, worktree)

        subtasks = {s["id"]: s["status"] for p in merged["phases"] for s in p["subtasks"]}
        assert subtasks["st-1"] == "failed"

    def test_multi_phase_merge(self):
        """Merge works across multiple phases."""
        main = {
            "phases": [
                {"id": "p1", "subtasks": [{"id": "st-1", "status": "completed"}]},
                {"id": "p2", "subtasks": [{"id": "st-2", "status": "in_progress"}]},
            ]
        }
        worktree = {
            "phases": [
                {"id": "p1", "subtasks": [{"id": "st-1", "status": "pending"}]},
                {"id": "p2", "subtasks": [{"id": "st-2", "status": "completed"}]},
            ]
        }

        merged = merge_test_plan(main, worktree)

        subtasks = {s["id"]: s["status"] for p in merged["phases"] for s in p["subtasks"]}
        assert subtasks["st-1"] == "completed"  # Regression prevented
        assert subtasks["st-2"] == "completed"  # Forward progress accepted

    def test_empty_phases(self):
        """Handles plans with no phases gracefully."""
        main = {"phases": []}
        worktree = {"phases": []}

        merged = merge_test_plan(main, worktree)
        assert merged["phases"] == []
