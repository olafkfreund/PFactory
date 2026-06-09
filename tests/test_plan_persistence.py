"""PlanService disk-backed persistence (survives pod restarts).

The plan store was in-memory only, so every pod restart wiped all plans. These
tests pin the opt-in (``PFACTORY_PLAN_PERSIST``) file persistence: a session
written by one service instance is reloaded by a fresh instance over the same
store dir, and persistence stays OFF (hermetic) by default.
"""

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

_PLAN = """# Greeting service
A tiny greeting endpoint.
## Acceptance Criteria
- greet returns a friendly hello
"""


def test_default_is_in_memory_no_disk(tmp_path):
    """Default (no env) writes nothing to disk — hermetic for unit tests."""
    svc = PlanService(store_dir=tmp_path)  # persist resolves from env (off)
    svc.ingest_text(_PLAN, title="greet")
    assert list(tmp_path.iterdir()) == []


def test_session_survives_a_fresh_service_instance(tmp_path):
    """A persisted session is reloaded by a new service over the same dir."""
    svc = PlanService(store_dir=tmp_path, persist=True)
    session = svc.ingest_text(_PLAN, title="greet")
    sid = session.session_id

    # A JSON file was written for it.
    assert (tmp_path / f"{sid}.json").exists()

    # A brand-new service (simulating a pod restart) reloads it from disk.
    reloaded = PlanService(store_dir=tmp_path, persist=True)
    got = reloaded.get(sid)
    assert got.session_id == sid
    assert got.plan.title == "greet"
    assert any(s["session_id"] == sid for s in reloaded.list_sessions())


def test_process_state_is_persisted(tmp_path):
    """Mutations after ingest (process → board_state) are persisted too."""
    svc = PlanService(store_dir=tmp_path, persist=True)
    sid = svc.ingest_text(_PLAN, title="greet").session_id
    svc.process(sid)

    reloaded = PlanService(store_dir=tmp_path, persist=True)
    got = reloaded.get(sid)
    assert got.status == "processed"
    assert got.board_state() == "human_review"  # processed → awaits a human


def test_corrupt_file_is_skipped_not_fatal(tmp_path):
    """An unreadable payload is skipped on load, not fatal."""
    (tmp_path / "broken.json").write_text("{ not valid json")
    good = PlanService(store_dir=tmp_path, persist=True)
    sid = good.ingest_text(_PLAN, title="greet").session_id

    reloaded = PlanService(store_dir=tmp_path, persist=True)
    # The good one loads; the broken file is ignored.
    assert reloaded.get(sid).session_id == sid
