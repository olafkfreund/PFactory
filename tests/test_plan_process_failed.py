"""A process() that raises leaves a persisted ``failed`` session, not a stuck one (#862)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.service import PlanService  # noqa: E402

_PLAN = "# Refund API\nAdd a refund endpoint.\n## Acceptance Criteria\n- Refunds work\n"


def _boom(*_a, **_k):
    raise ValueError("boom")


def test_a_raising_process_saves_failed_and_survives_restart(tmp_path, monkeypatch):
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    monkeypatch.setattr(svc, "_decompose", _boom)

    with pytest.raises(ValueError, match="boom"):
        svc.process(sid)

    session = svc.get(sid)
    assert session.status == "failed"
    assert session.process_error == "ValueError"
    assert session.board_state() == "human_review"
    assert PlanService(store_dir=tmp_path, persist=True).get(sid).status == "failed"


def test_reprocessing_a_failed_session_clears_the_error(tmp_path, monkeypatch):
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    with monkeypatch.context() as m:
        m.setattr(svc, "_decompose", _boom)
        with pytest.raises(ValueError):
            svc.process(sid)

    session = svc.process(sid)

    assert session.status == "processed"
    assert session.process_error is None


@pytest.mark.parametrize("terminal", ["emitted", "discarded"])
def test_failed_reprocess_keeps_a_terminal_status(tmp_path, monkeypatch, terminal):
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    svc.get(sid).status = terminal
    monkeypatch.setattr(svc, "_decompose", _boom)

    with pytest.raises(ValueError):
        svc.process(sid)

    assert svc.get(sid).status == terminal
    assert svc.get(sid).process_error is None
