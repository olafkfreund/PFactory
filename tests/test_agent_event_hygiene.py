"""
Tests for Issue #14 — task event hygiene.

Covers two behaviours:

1. **Dedup.** `AgentService._safe_emit_task_update` suppresses re-emission when
   the structural signature (phase, progress, subtasks…) is unchanged. The
   3-second periodic `_sync_worktree_files` tick during a long phase no
   longer floods the WebSocket with identical events.

2. **Signature semantics.** The dedup signature deliberately excludes the
   per-tick `message` text and `sequenceNumber` (those drift every call by
   design) and includes `subtasks` (checkbox transitions matter).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Add web-server source root to path so we can import the service module
_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
sys.path.insert(0, str(_WEB_SERVER))

from server.services.agent_service import AgentService, _dedup_signature

# ---------------------------------------------------------------------------
# Pure-function tests on the signature
# ---------------------------------------------------------------------------


class TestDedupSignature:
    """_dedup_signature(payload) — pure function."""

    BASE_PAYLOAD = {
        "executionProgress": {
            "phase": "coding",
            "phaseProgress": 50,
            "overallProgress": 30,
            "currentSubtask": "1.2",
            "message": "Working on it",
            "sequenceNumber": 7,
            "startedAt": "2026-05-21T11:00:00",
        },
        "phase": "coding",
        "subtasksCompleted": 2,
        "subtasksTotal": 5,
        "subtasks": [
            {"id": "1.1", "status": "completed"},
            {"id": "1.2", "status": "in_progress"},
        ],
    }

    def test_identical_payloads_produce_identical_signatures(self) -> None:
        import copy
        a = _dedup_signature(self.BASE_PAYLOAD)
        b = _dedup_signature(copy.deepcopy(self.BASE_PAYLOAD))
        assert a == b

    def test_message_change_does_not_change_signature(self) -> None:
        # Message drifts every QA tick — must be excluded.
        modified = {**self.BASE_PAYLOAD,
                    "executionProgress": {**self.BASE_PAYLOAD["executionProgress"],
                                          "message": "qa review file 3/10"}}
        assert _dedup_signature(modified) == _dedup_signature(self.BASE_PAYLOAD)

    def test_sequence_number_change_does_not_change_signature(self) -> None:
        modified = {**self.BASE_PAYLOAD,
                    "executionProgress": {**self.BASE_PAYLOAD["executionProgress"],
                                          "sequenceNumber": 42}}
        assert _dedup_signature(modified) == _dedup_signature(self.BASE_PAYLOAD)

    def test_started_at_change_does_not_change_signature(self) -> None:
        modified = {**self.BASE_PAYLOAD,
                    "executionProgress": {**self.BASE_PAYLOAD["executionProgress"],
                                          "startedAt": "1999-01-01T00:00:00"}}
        assert _dedup_signature(modified) == _dedup_signature(self.BASE_PAYLOAD)

    def test_phase_change_changes_signature(self) -> None:
        modified = {**self.BASE_PAYLOAD,
                    "phase": "qa_review",
                    "executionProgress": {**self.BASE_PAYLOAD["executionProgress"],
                                          "phase": "qa_review"}}
        assert _dedup_signature(modified) != _dedup_signature(self.BASE_PAYLOAD)

    def test_progress_change_changes_signature(self) -> None:
        modified = {**self.BASE_PAYLOAD,
                    "executionProgress": {**self.BASE_PAYLOAD["executionProgress"],
                                          "phaseProgress": 80}}
        assert _dedup_signature(modified) != _dedup_signature(self.BASE_PAYLOAD)

    def test_subtask_status_change_changes_signature(self) -> None:
        # Checkbox transitions are meaningful — must defeat the dedup gate.
        modified = {**self.BASE_PAYLOAD,
                    "subtasks": [
                        {"id": "1.1", "status": "completed"},
                        {"id": "1.2", "status": "completed"},  # was in_progress
                    ]}
        assert _dedup_signature(modified) != _dedup_signature(self.BASE_PAYLOAD)

    def test_empty_payload_signature_is_stable(self) -> None:
        # Empty payloads should produce a deterministic signature (all Nones).
        empty = {}
        sig = _dedup_signature(empty)
        assert sig == (None, None, None, None, None, None, None, ())


# ---------------------------------------------------------------------------
# Helper-method tests on AgentService
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> AgentService:
    """Build an AgentService without running its full __init__ (which talks to settings).

    We just need the dedup state — bypass settings-dependent setup.
    """
    svc = AgentService.__new__(AgentService)
    svc._last_emitted_task_update = {}
    svc._task_build_progress_offset = {}
    return svc


_TASK = "proj-001:spec-001"


class TestSafeEmitTaskUpdateDedup:
    """_safe_emit_task_update suppresses identical re-emissions."""

    @pytest.mark.asyncio
    async def test_identical_payload_emits_once(self, service: AgentService) -> None:
        payload = {
            "executionProgress": {"phase": "qa_review", "phaseProgress": 100,
                                  "overallProgress": 80, "currentSubtask": None,
                                  "message": "qa", "sequenceNumber": 1,
                                  "startedAt": "now"},
            "phase": "qa_review",
            "subtasks": [{"id": "1.1", "status": "in_progress"}],
        }
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(_TASK, payload)
            await service._safe_emit_task_update(_TASK, payload)
            assert mock_emit.await_count == 1

    @pytest.mark.asyncio
    async def test_twenty_six_identical_emits_collapse_to_one(self, service: AgentService) -> None:
        """The original symptom — 26 QA-review ticks must yield one network emit."""
        payload = {
            "executionProgress": {"phase": "qa_review", "phaseProgress": 100,
                                  "overallProgress": 80, "currentSubtask": None,
                                  "message": "qa", "sequenceNumber": 1,
                                  "startedAt": "t"},
            "phase": "qa_review",
            "subtasks": [],
        }
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            for _ in range(26):
                await service._safe_emit_task_update(_TASK, payload)
            assert mock_emit.await_count == 1

    @pytest.mark.asyncio
    async def test_phase_change_emits_again(self, service: AgentService) -> None:
        payload_a = {"phase": "coding", "executionProgress": {"phase": "coding"}}
        payload_b = {"phase": "qa_review", "executionProgress": {"phase": "qa_review"}}
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(_TASK, payload_a)
            await service._safe_emit_task_update(_TASK, payload_b)
            assert mock_emit.await_count == 2

    @pytest.mark.asyncio
    async def test_subtask_status_change_emits_again(self, service: AgentService) -> None:
        common_exec = {"phase": "coding", "phaseProgress": 50, "overallProgress": 50}
        a = {"phase": "coding", "executionProgress": common_exec,
             "subtasks": [{"id": "1.1", "status": "in_progress"}]}
        b = {"phase": "coding", "executionProgress": common_exec,
             "subtasks": [{"id": "1.1", "status": "completed"}]}
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(_TASK, a)
            await service._safe_emit_task_update(_TASK, b)
            assert mock_emit.await_count == 2

    @pytest.mark.asyncio
    async def test_message_only_change_is_suppressed(self, service: AgentService) -> None:
        base_exec = {"phase": "qa_review", "phaseProgress": 100, "overallProgress": 80,
                     "currentSubtask": None, "sequenceNumber": 1, "startedAt": "t"}
        a = {"phase": "qa_review",
             "executionProgress": {**base_exec, "message": "Starting QA"},
             "subtasks": []}
        b = {"phase": "qa_review",
             "executionProgress": {**base_exec, "message": "qa review file 3/10"},
             "subtasks": []}
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(_TASK, a)
            await service._safe_emit_task_update(_TASK, b)
            assert mock_emit.await_count == 1, \
                "message-only drift must be suppressed (it floods during QA)"

    @pytest.mark.asyncio
    async def test_sequence_number_drift_is_suppressed(self, service: AgentService) -> None:
        common = {"phase": "coding",
                  "executionProgress": {"phase": "coding", "phaseProgress": 50,
                                        "overallProgress": 30, "currentSubtask": None,
                                        "message": "x", "startedAt": "t"}}
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(
                _TASK, {**common, "executionProgress": {**common["executionProgress"], "sequenceNumber": 1}})
            await service._safe_emit_task_update(
                _TASK, {**common, "executionProgress": {**common["executionProgress"], "sequenceNumber": 99}})
            assert mock_emit.await_count == 1

    @pytest.mark.asyncio
    async def test_different_tasks_dedup_independently(self, service: AgentService) -> None:
        # Two concurrent tasks with identical payloads — both emit.
        payload = {"phase": "coding", "executionProgress": {"phase": "coding"}}
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update("task-A", payload)
            await service._safe_emit_task_update("task-B", payload)
            assert mock_emit.await_count == 2

    @pytest.mark.asyncio
    async def test_eviction_re_arms_dedup(self, service: AgentService) -> None:
        # After explicit eviction (the cleanup-block pattern), the next identical
        # emit goes through. This is the path the monitor exit branch uses.
        payload = {"phase": "completed",
                   "executionProgress": {"phase": "completed", "phaseProgress": 100,
                                         "overallProgress": 100}}
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(_TASK, payload)
            service._last_emitted_task_update.pop(_TASK, None)  # mimic cleanup
            await service._safe_emit_task_update(_TASK, payload)
            assert mock_emit.await_count == 2


class TestSafeEmitTaskStatusNoDedup:
    """_safe_emit_task_status is a pass-through (no dedup)."""

    @pytest.mark.asyncio
    async def test_duplicate_status_emits_both_times(self, service: AgentService) -> None:
        with patch(
            "server.services.agent_service.emit_task_status",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_status(_TASK, "human_review", "completed")
            await service._safe_emit_task_status(_TASK, "human_review", "completed")
            assert mock_emit.await_count == 2


class TestUpdatePlanStatusEmitEvents:
    """_update_plan_status with emit_events=False is silent (Issue #14 terminal collapse)."""

    @pytest.mark.asyncio
    async def test_emit_events_false_suppresses_emissions(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        """Terminal call (emit_events=False) must NOT fire task:status or task:update.

        The _monitor_process exit branch passes emit_events=False so that the
        subsequent _emit_progress(COMPLETED, ...) is the SINGLE terminal
        emission. Without this gate we got the 5-event flurry.
        """
        # Set up a minimal valid test_plan.json
        spec_dir = tmp_path / ".pfactory" / "specs" / "spec-001"
        spec_dir.mkdir(parents=True)
        plan_file = spec_dir / "test_plan.json"
        plan_file.write_text(
            '{"status": "in_progress", '
            '"phases": [{"subtasks": [{"id": "1.1", "status": "completed"}]}]}'
        )

        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_update, patch(
            "server.services.agent_service.emit_task_status",
            new_callable=AsyncMock,
        ) as mock_status:
            await service._update_plan_status(
                tmp_path, "spec-001", "completed", _TASK, emit_events=False
            )
            # File should be written (status updated), but NO emissions.
            assert mock_update.await_count == 0
            assert mock_status.await_count == 0
            # Confirm the file write actually happened
            import json
            saved = json.loads(plan_file.read_text())
            assert saved["status"] in {"completed", "human_review"}

    @pytest.mark.asyncio
    async def test_emit_events_true_default_still_emits(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        """The 5 mid-run callers (default emit_events=True) keep emitting.

        And the terminal payload is enriched with executionProgress so the
        log line shows phase=completed (not phase=N/A).
        """
        spec_dir = tmp_path / ".pfactory" / "specs" / "spec-001"
        spec_dir.mkdir(parents=True)
        plan_file = spec_dir / "test_plan.json"
        plan_file.write_text(
            '{"status": "in_progress", '
            '"phases": [{"subtasks": [{"id": "1.1", "status": "completed"}]}]}'
        )

        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_update, patch(
            "server.services.agent_service.emit_task_status",
            new_callable=AsyncMock,
        ) as mock_status:
            await service._update_plan_status(
                tmp_path, "spec-001", "completed", _TASK  # default emit_events=True
            )
            # Both must fire when emit_events=True.
            assert mock_status.await_count == 1
            assert mock_update.await_count == 1
            # The task:update payload MUST carry an executionProgress block
            # (this is the Issue #14 fix that kills "phase: N/A" log lines).
            payload = mock_update.await_args.args[1]
            assert "executionProgress" in payload, \
                "payload must include executionProgress (Issue #14)"
            assert payload["executionProgress"]["phase"] == "completed"
            assert payload["executionProgress"]["overallProgress"] == 100
            assert payload.get("phase") == "completed"


class TestBuildProgressTailEmit:
    """Tier B auto-reload — _sync_worktree_files streams build-progress.txt
    deltas as task:log events so the kanban detail view scrolls the agent's
    narrative in real time.

    These tests exercise the offset-tracking logic directly rather than
    invoking the full ``_sync_worktree_files`` (which depends on settings,
    workspace, etc.) — the contract we care about is:

      1. First read of a file emits every line.
      2. Subsequent reads with no new bytes emit nothing.
      3. Subsequent reads after append emit only the new tail.
      4. File truncation resets the offset to 0.
    """

    @pytest.mark.asyncio
    async def test_first_read_emits_all_lines(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        bp = tmp_path / "build-progress.txt"
        bp.write_text("line 1\nline 2\nline 3\n")
        with patch(
            "server.websockets.events.emit_task_log",
            new_callable=AsyncMock,
        ) as mock_emit:
            await _tail_build_progress(service, bp, _TASK)
            assert mock_emit.await_count == 3
            assert service._task_build_progress_offset[_TASK] == bp.stat().st_size

    @pytest.mark.asyncio
    async def test_unchanged_file_emits_nothing(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        bp = tmp_path / "build-progress.txt"
        bp.write_text("line 1\nline 2\n")
        with patch(
            "server.websockets.events.emit_task_log",
            new_callable=AsyncMock,
        ) as mock_emit:
            await _tail_build_progress(service, bp, _TASK)
            initial = mock_emit.await_count
            # Second sync tick — file hasn't grown
            await _tail_build_progress(service, bp, _TASK)
            assert mock_emit.await_count == initial  # no new emits

    @pytest.mark.asyncio
    async def test_append_emits_only_new_tail(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        bp = tmp_path / "build-progress.txt"
        bp.write_text("first\nsecond\n")
        with patch(
            "server.websockets.events.emit_task_log",
            new_callable=AsyncMock,
        ) as mock_emit:
            await _tail_build_progress(service, bp, _TASK)
            assert mock_emit.await_count == 2
            # Agent appends two more lines
            with bp.open("a", encoding="utf-8") as fh:
                fh.write("third\nfourth\n")
            await _tail_build_progress(service, bp, _TASK)
            assert mock_emit.await_count == 4
            # The two new emits carry the new content
            recent_calls = [c.args[1] for c in mock_emit.await_args_list[-2:]]
            assert recent_calls == ["third", "fourth"]

    @pytest.mark.asyncio
    async def test_truncation_resets_offset(
        self, service: AgentService, tmp_path: Path
    ) -> None:
        bp = tmp_path / "build-progress.txt"
        bp.write_text("alpha\nbeta\ngamma\n")
        with patch(
            "server.websockets.events.emit_task_log",
            new_callable=AsyncMock,
        ) as mock_emit:
            await _tail_build_progress(service, bp, _TASK)
            # Worktree recreated — file shrinks
            bp.write_text("newrun\n")
            await _tail_build_progress(service, bp, _TASK)
            # Last emit must be the fresh first line, not a stale offset read
            assert mock_emit.await_args_list[-1].args[1] == "newrun"


async def _tail_build_progress(
    service: AgentService, bp_main: Path, task_id: str
) -> None:
    """Mirror of the inline tail-and-emit block in _sync_worktree_files.

    Kept here as a test-only helper so we can exercise the offset logic
    without booting the full service. Any change to the production block
    in agent_service.py must be reflected here, or the tests rot — kept
    short on purpose so drift is obvious in code review.
    """
    if not bp_main.exists():
        return
    current_size = bp_main.stat().st_size
    prev_offset = service._task_build_progress_offset.get(task_id, 0)
    if current_size < prev_offset:
        prev_offset = 0
    if current_size > prev_offset:
        with bp_main.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(prev_offset)
            new_text = fh.read()
        service._task_build_progress_offset[task_id] = current_size
        from server.websockets.events import emit_task_log
        for line in new_text.splitlines():
            stripped = line.rstrip()
            if stripped:
                await emit_task_log(task_id, stripped)


class TestSafeEmitTaskUpdateForceBypass:
    """``force=True`` bypasses structural dedup.

    The 3-second worktree-sync tick passes ``force=True`` when files were
    actually copied, because file CONTENT (task_logs.json, build-progress.txt)
    may have changed even though the dedup signature (phase, progress,
    subtask-status) did not. Without this escape hatch the kanban board
    freezes for the full duration of a long subtask.
    """

    @pytest.mark.asyncio
    async def test_force_true_bypasses_dedup(self, service: AgentService) -> None:
        payload = {
            "executionProgress": {"phase": "coding", "phaseProgress": 50,
                                  "overallProgress": 30, "currentSubtask": "1.1",
                                  "message": "0/3 subtasks completed",
                                  "sequenceNumber": 1, "startedAt": "t"},
            "phase": "coding",
            "subtasks": [{"id": "1.1", "status": "pending"}],
        }
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            # First emit: normal dedup path.
            await service._safe_emit_task_update(_TASK, payload)
            # Second emit with identical payload but force=True: must go through.
            await service._safe_emit_task_update(_TASK, payload, force=True)
            assert mock_emit.await_count == 2

    @pytest.mark.asyncio
    async def test_force_false_still_dedups(self, service: AgentService) -> None:
        """Regression guard — default behaviour unchanged for other callers."""
        payload = {
            "executionProgress": {"phase": "coding", "phaseProgress": 50,
                                  "overallProgress": 30, "currentSubtask": "1.1",
                                  "message": "0/3 subtasks completed",
                                  "sequenceNumber": 1, "startedAt": "t"},
            "phase": "coding",
            "subtasks": [{"id": "1.1", "status": "pending"}],
        }
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            await service._safe_emit_task_update(_TASK, payload)
            await service._safe_emit_task_update(_TASK, payload)  # implicit force=False
            assert mock_emit.await_count == 1

    @pytest.mark.asyncio
    async def test_long_subtask_sync_heartbeat_unfrozen(
        self, service: AgentService
    ) -> None:
        """Simulates the user-reported symptom: 20 sync ticks during a long
        subtask, all with identical structural payload. Without ``force=True``
        the frontend sees ONE event and the kanban freezes. With ``force=True``
        it sees all 20 — the demo's auto-reload behaviour.
        """
        payload = {
            "executionProgress": {"phase": "coding", "phaseProgress": 50,
                                  "overallProgress": 30, "currentSubtask": "1.1",
                                  "message": "0/3 subtasks completed",
                                  "sequenceNumber": 1, "startedAt": "t"},
            "phase": "coding",
            "subtasks": [{"id": "1.1", "status": "pending"}],
        }
        with patch(
            "server.services.agent_service.emit_task_update",
            new_callable=AsyncMock,
        ) as mock_emit:
            for _ in range(20):
                await service._safe_emit_task_update(_TASK, payload, force=True)
            assert mock_emit.await_count == 20
