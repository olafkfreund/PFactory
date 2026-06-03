"""
Tests for analysis.insight_extractor.extract_session_insights_bulk (Issue #11).

The bulk path is an opt-in primitive shipped without a production caller —
these tests verify the threshold gate, kill-switch, sequential fallback,
and per-entry generic-fallback on batch errors / timeouts / missing API key.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Force SDK_AVAILABLE to True for the module so is_extraction_enabled() returns
# True in tests that exercise it. Most tests don't need this — they patch
# extract_session_insights directly — but the imports must succeed first.
from analysis import insight_extractor as ie  # noqa: E402


def _completion(subtask_id: str, *, success: bool = True) -> dict:
    return {
        "subtask_id": subtask_id,
        "session_num": 1,
        "commit_before": "aaa1111",
        "commit_after": "bbb2222",
        "success": success,
        "recovery_manager": None,
    }


# ---------------------------------------------------------------------------
# Threshold + kill-switch
# ---------------------------------------------------------------------------


class TestThreshold:
    async def test_below_threshold_uses_sequential_path(self, monkeypatch, tmp_path):
        """1 completion → sequential path (threshold defaults to 2)."""
        monkeypatch.delenv("PFACTORY_BATCH_MIN_JOBS", raising=False)
        monkeypatch.delenv("PFACTORY_BATCH_DISABLE", raising=False)

        seq_mock = AsyncMock(return_value={"file_insights": [{"sequential": True}]})
        with patch.object(ie, "extract_session_insights", seq_mock):
            with patch("core.batch.submit_batch", AsyncMock()) as batch_mock:
                results = await ie.extract_session_insights_bulk(
                        [_completion("t1")],
                        project_dir=tmp_path,
                        spec_dir=tmp_path,
                        api_key="test",
                    )

        assert "t1" in results
        seq_mock.assert_awaited_once()
        batch_mock.assert_not_called()

    async def test_at_threshold_uses_batch_path(self, monkeypatch, tmp_path):
        """2 completions → batch path engages."""
        monkeypatch.delenv("PFACTORY_BATCH_MIN_JOBS", raising=False)
        monkeypatch.delenv("PFACTORY_BATCH_DISABLE", raising=False)

        # Provide a fake inputs gather + parse path.
        with patch.object(
            ie,
            "gather_extraction_inputs",
            return_value={
                "subtask_id": "_",
                "subtask_description": "_",
                "session_num": 1,
                "success": True,
                "diff": "diff",
                "changed_files": [],
                "commit_messages": [],
                "attempt_history": [],
            },
        ), patch.object(
            ie, "_build_extraction_prompt", return_value="extract insights"
        ), patch("core.batch.submit_batch", AsyncMock(return_value="msgbatch_test")
        ) as submit_mock, patch("core.batch.await_batch",
            AsyncMock(
                return_value=[
                    SimpleNamespace(
                        custom_id="t1",
                        status="succeeded",
                        content='{"file_insights": [{"path": "x.py"}]}',
                        usage={"service_tier": "batch"},
                        error=None,
                    ),
                    SimpleNamespace(
                        custom_id="t2",
                        status="succeeded",
                        content='{"file_insights": []}',
                        usage={"service_tier": "batch"},
                        error=None,
                    ),
                ]
            ),
        ), patch("core.batch.extract_savings",
            return_value={
                "succeeded": 2,
                "errored": 0,
                "service_tiers": ["batch"],
                "estimated_saving_pct": 0.5,
            },
        ):
            results = await ie.extract_session_insights_bulk(
                    [_completion("t1"), _completion("t2")],
                    project_dir=tmp_path,
                    spec_dir=tmp_path,
                    api_key="test",
                )

        submit_mock.assert_awaited_once()
        assert set(results.keys()) == {"t1", "t2"}
        assert results["t1"]["file_insights"] == [{"path": "x.py"}]
        # Metadata fields stamped on each entry.
        assert results["t1"]["subtask_id"] == "t1"
        assert results["t1"]["success"] is True

    async def test_kill_switch_forces_sequential(self, monkeypatch, tmp_path):
        """PFACTORY_BATCH_DISABLE=1 forces sequential even at high N."""
        monkeypatch.setenv("PFACTORY_BATCH_DISABLE", "1")

        seq_mock = AsyncMock(return_value={"file_insights": []})
        with patch.object(ie, "extract_session_insights", seq_mock):
            with patch("core.batch.submit_batch", AsyncMock()) as batch_mock:
                await ie.extract_session_insights_bulk(
                        [_completion(f"t{i}") for i in range(5)],
                        project_dir=tmp_path,
                        spec_dir=tmp_path,
                        api_key="test",
                    )
        assert seq_mock.await_count == 5
        batch_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailurePaths:
    async def test_partial_failure_falls_back_to_generic_per_entry(
        self, monkeypatch, tmp_path
    ):
        """Errored entries surface as generic_insights, not exceptions."""
        monkeypatch.delenv("PFACTORY_BATCH_DISABLE", raising=False)

        with patch.object(
            ie,
            "gather_extraction_inputs",
            return_value={
                "subtask_id": "_",
                "subtask_description": "_",
                "session_num": 1,
                "success": True,
                "diff": "",
                "changed_files": [],
                "commit_messages": [],
                "attempt_history": [],
            },
        ), patch.object(
            ie, "_build_extraction_prompt", return_value="extract"
        ), patch("core.batch.submit_batch", AsyncMock(return_value="msgbatch_test")
        ), patch("core.batch.await_batch",
            AsyncMock(
                return_value=[
                    SimpleNamespace(
                        custom_id="t1",
                        status="succeeded",
                        content='{"file_insights": [{"path": "a.py"}]}',
                        usage={"service_tier": "batch"},
                        error=None,
                    ),
                    SimpleNamespace(
                        custom_id="t2",
                        status="errored",
                        content=None,
                        usage=None,
                        error="rate limited",
                    ),
                ]
            ),
        ), patch("core.batch.extract_savings",
            return_value={
                "succeeded": 1,
                "errored": 1,
                "service_tiers": ["batch"],
                "estimated_saving_pct": 0.5,
            },
        ):
            results = await ie.extract_session_insights_bulk(
                    [_completion("t1"), _completion("t2")],
                    project_dir=tmp_path,
                    spec_dir=tmp_path,
                    api_key="test",
                )

        # t1 got the parsed insight; t2 got the generic shape.
        assert results["t1"]["file_insights"] == [{"path": "a.py"}]
        # Generic insight has empty file_insights and approach_outcome set.
        assert results["t2"]["file_insights"] == []
        assert results["t2"]["approach_outcome"]["approach_used"].startswith(
            "Implemented subtask"
        )

    async def test_timeout_falls_back_to_sequential_for_all(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PFACTORY_BATCH_DISABLE", raising=False)

        seq_mock = AsyncMock(return_value={"file_insights": [{"fallback": True}]})
        with patch.object(
            ie,
            "gather_extraction_inputs",
            return_value={
                "subtask_id": "_",
                "subtask_description": "_",
                "session_num": 1,
                "success": True,
                "diff": "",
                "changed_files": [],
                "commit_messages": [],
                "attempt_history": [],
            },
        ), patch.object(
            ie, "_build_extraction_prompt", return_value="extract"
        ), patch("core.batch.submit_batch", AsyncMock(return_value="msgbatch_test")
        ), patch("core.batch.await_batch",
            AsyncMock(side_effect=TimeoutError("did not complete within 0s")),
        ), patch.object(
            ie, "extract_session_insights", seq_mock
        ):
            results = await ie.extract_session_insights_bulk(
                    [_completion("t1"), _completion("t2"), _completion("t3")],
                    project_dir=tmp_path,
                    spec_dir=tmp_path,
                    api_key="test",
                )

        # All 3 fell back to sequential path on timeout.
        assert seq_mock.await_count == 3
        assert set(results.keys()) == {"t1", "t2", "t3"}

    async def test_missing_api_key_falls_back_to_sequential(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("PFACTORY_BATCH_DISABLE", raising=False)

        seq_mock = AsyncMock(return_value={"file_insights": [{"no_key": True}]})

        with patch.object(
            ie,
            "gather_extraction_inputs",
            return_value={
                "subtask_id": "_",
                "subtask_description": "_",
                "session_num": 1,
                "success": True,
                "diff": "",
                "changed_files": [],
                "commit_messages": [],
                "attempt_history": [],
            },
        ), patch.object(
            ie, "_build_extraction_prompt", return_value="extract"
        ), patch("core.batch.submit_batch",
            AsyncMock(side_effect=RuntimeError("Anthropic API key required")),
        ), patch.object(
            ie, "extract_session_insights", seq_mock
        ):
            results = await ie.extract_session_insights_bulk(
                    [_completion("t1"), _completion("t2")],
                    project_dir=tmp_path,
                    spec_dir=tmp_path,
                    api_key=None,
                )

        # API key missing → sequential fallback for all entries.
        assert seq_mock.await_count == 2
        assert set(results.keys()) == {"t1", "t2"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_completions_returns_empty_dict(self, tmp_path):
        results = await ie.extract_session_insights_bulk(
                [], project_dir=tmp_path, spec_dir=tmp_path
            )
        assert results == {}

    async def test_no_changes_entry_gets_generic_insights(self, monkeypatch, tmp_path):
        """commit_before == commit_after → skip batch entry, use generic."""
        monkeypatch.delenv("PFACTORY_BATCH_DISABLE", raising=False)

        comps = [
            {**_completion("t1"), "commit_before": "abc", "commit_after": "abc"},
            {**_completion("t2"), "commit_before": "abc", "commit_after": "abc"},
        ]
        # Both entries skipped → batch never submitted; both get generic.
        with patch("core.batch.submit_batch", AsyncMock()) as submit_mock:
            results = await ie.extract_session_insights_bulk(
                    comps,
                    project_dir=tmp_path,
                    spec_dir=tmp_path,
                    api_key="test",
                )
        submit_mock.assert_not_called()
        assert set(results.keys()) == {"t1", "t2"}
        assert all(r["file_insights"] == [] for r in results.values())
