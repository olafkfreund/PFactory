"""Tests for downstream completion-event sync + reconcile (epic #65, child 9)."""

from __future__ import annotations

import pytest
from plan.emit.contract_sync import (
    ContractSyncRegistry,
    SyncError,
    classify_outcome,
    parse_completion_event,
)


def _evt(service: str, status: str, key: str = "42") -> dict:
    return {"correlation_key": key, "service": service, "status": status,
            "task_id": "t1", "phase": "qa", "updated_at": "2026-06-06T00:00:00Z"}


# ── parsing ────────────────────────────────────────────────────────────────


def test_parse_requires_fields() -> None:
    with pytest.raises(SyncError, match="missing required"):
        parse_completion_event({"service": "aifactory"})
    with pytest.raises(SyncError, match="must be an object"):
        parse_completion_event("nope")


def test_parse_normalizes_case() -> None:
    ev = parse_completion_event(_evt("AIFactory", "QA_Approved"))
    assert ev.service == "aifactory" and ev.status == "qa_approved"


# ── classification ───────────────────────────────────────────────────────────


def test_classify_aifactory() -> None:
    assert classify_outcome("aifactory", "qa_approved") == "success"
    assert classify_outcome("aifactory", "qa_failed") == "failure"
    assert classify_outcome("aifactory", "coding") == "in_progress"


def test_classify_tfactory() -> None:
    assert classify_outcome("tfactory", "triaged") == "success"
    assert classify_outcome("tfactory", "triager_failed") == "failure"


def test_classify_generic_keyword_fallback() -> None:
    assert classify_outcome("unknown", "something_failed") == "failure"
    assert classify_outcome("unknown", "all_done") == "success"
    assert classify_outcome("unknown", "running") == "in_progress"


# ── reconcile via registry ──────────────────────────────────────────────────


def test_aifactory_failure_needs_handback() -> None:
    reg = ContractSyncRegistry()
    state = reg.apply(_evt("aifactory", "qa_failed"))
    assert state.outcome == "failure" and state.needs_handback
    assert reg.needing_handback() == [state]


def test_tfactory_rejection_needs_handback() -> None:
    reg = ContractSyncRegistry()
    reg.apply(_evt("aifactory", "qa_approved"))
    state = reg.apply(_evt("tfactory", "rejected"))
    assert state.needs_handback


def test_full_success_path() -> None:
    reg = ContractSyncRegistry()
    reg.apply(_evt("aifactory", "qa_approved"))
    state = reg.apply(_evt("tfactory", "triaged"))
    assert state.outcome == "success"
    assert not state.needs_handback
    assert reg.needing_handback() == []


def test_in_progress_until_aifactory_done() -> None:
    reg = ContractSyncRegistry()
    state = reg.apply(_evt("aifactory", "coding"))
    assert state.outcome == "in_progress"
    assert not state.needs_handback


def test_history_accumulates_per_key() -> None:
    reg = ContractSyncRegistry()
    reg.apply(_evt("aifactory", "coding"))
    state = reg.apply(_evt("aifactory", "qa_approved"))
    assert len(state.history) == 2
    assert reg.get("42") is state


def test_separate_keys_tracked_independently() -> None:
    reg = ContractSyncRegistry()
    reg.apply(_evt("aifactory", "qa_failed", key="1"))
    reg.apply(_evt("aifactory", "qa_approved", key="2"))
    assert reg.get("1").needs_handback
    assert not reg.get("2").needs_handback
    assert [s.correlation_key for s in reg.needing_handback()] == ["1"]


# ── feedback for the planner (#998) ────────────────────────────────────────

from plan.emit.contract_sync import ContractSyncState, fold_event  # noqa: E402


def test_feedback_failure_reason_cost_and_unverified() -> None:
    st = ContractSyncState(correlation_key="42")
    fold_event(
        st,
        {
            **_evt("aifactory", "qa_failed"),
            "halt_reason": "guardrail\x00 hit",
            "usage": {"cost_usd": 1.5},
        },
    )
    fold_event(st, {**_evt("aifactory", "qa_approved"), "usage": {"cost_usd": 2.0}})
    fold_event(
        st,
        {
            **_evt("tfactory", "triaged"),
            "outcome": "failure",
            "id": "e1",
            "verification": {
                "traceability": [
                    {"ac_id": "AC#1", "status": "passed"},
                    {"ac_id": "AC#2", "status": "failed"},
                ]
            },
        },
    )
    assert st.handback_count == 2  # qa_failed + explicit outcome=failure on "triaged"
    assert st.handbacks[0]["reason"] == "guardrail hit"
    assert st.unverified_criteria == ["AC#2"]
    assert st.actual_cost_usd == 2.0  # latest snapshot replaces, not adds


def test_feedback_retry_is_not_double_counted_and_unknown_stays_none() -> None:
    st = ContractSyncState(correlation_key="42")
    fold_event(st, {**_evt("tfactory", "failed"), "id": "same"})
    fold_event(st, {**_evt("tfactory", "failed"), "id": "same"})
    fold_event(st, {**_evt("tfactory", "failed"), "id": "other"})
    assert st.handback_count == 2
    assert st.actual_cost_usd is None and st.unverified_criteria is None


def test_feedback_replay_does_not_overwrite_newer_values() -> None:
    st = ContractSyncState(correlation_key="42")
    a = {**_evt("aifactory", "running"), "id": "a", "usage": {"cost_usd": 1.0}}
    fold_event(st, a)
    fold_event(st, {**_evt("aifactory", "qa_approved"), "id": "b", "usage": {"cost_usd": 2.0}})
    fold_event(st, a)  # late retry of the older event
    assert st.actual_cost_usd == 2.0
