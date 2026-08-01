"""phase_to_status must never silently report "in_progress" for an unmapped
TaskPhase (Factory #431).

The mapping in ``phase_to_status`` covers every current ``TaskPhase``
member, so the fallback is unreachable *today*. But if a future phase is
added without a matching entry, the frontend's kanban board has no
"unknown" column -- silently reporting "in_progress" would tell the user a
task is actively being worked on when nobody is watching it. The honest
fallback is "human_review": put it in front of a human, the same reasoning
already used for COMPLETED/FAILED.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.services.task_models import TaskPhase, phase_to_status  # noqa: E402


def test_every_task_phase_is_mapped():
    """Drift guard: every TaskPhase member has an explicit status mapping."""
    for phase in TaskPhase:
        assert phase_to_status(phase) in {
            "backlog",
            "in_progress",
            "ai_review",
            "human_review",
            "done",
        }


def test_unmapped_phase_routes_to_human_review_not_in_progress(caplog):
    """An unrecognized phase must not silently look like active progress."""

    class _NotARealPhase:
        """Stand-in for a future TaskPhase member missing from the mapping."""

        def __repr__(self) -> str:
            return "TaskPhase.FUTURE_UNKNOWN"

    with caplog.at_level(logging.WARNING):
        result = phase_to_status(_NotARealPhase())  # type: ignore[arg-type]

    assert result == "human_review"
    assert result != "in_progress"
    assert any("unmapped" in record.message.lower() for record in caplog.records)
