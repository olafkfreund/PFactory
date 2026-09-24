"""No lost session writes across replicas or threads (#758).

Every session write is a compare-and-set on the version it was read at. A write
from an older copy raises ``StaleSessionError`` and leaves the stored row
alone; every other store failure keeps ``_save``'s never-raises contract.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.service import PlanService, PlanServiceError, StaleSessionError  # noqa: E402
from tests.fake_session_store import FakeSessionStore  # noqa: E402

_PLAN = "# Refund API\nAdd a refund endpoint.\n## Acceptance Criteria\n- Refunds work\n"


def test_stale_error_is_a_plan_service_error():
    assert issubclass(StaleSessionError, PlanServiceError)


def test_a_write_from_an_older_copy_is_refused_and_the_cache_refreshed():
    store = FakeSessionStore()
    a = PlanService(session_store=store)
    b = PlanService(session_store=store)
    sid = a.ingest_text(_PLAN, title="Refund API").session_id

    old = b.get(sid)  # replica B reads
    a.discard(sid, actor="olaf", reason="duplicate")  # replica A writes
    stored = store.rows[sid]

    old.selected_category = "overwrite"
    with pytest.raises(StaleSessionError, match="another replica"):
        b._save(old)

    assert store.rows[sid] == stored  # the discard survived
    assert b._sessions[sid].status == "discarded"  # cache refreshed from the store


def test_a_write_to_a_row_deleted_elsewhere_says_so():
    store = FakeSessionStore()
    svc = PlanService(session_store=store)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    session = svc.get(sid)
    del store.rows[sid]  # removed by another replica

    with pytest.raises(StaleSessionError, match="deleted on another replica"):
        svc._save(session)
    assert sid not in store.rows  # not resurrected


def test_two_copies_in_one_process_cannot_overwrite_each_other():
    store = FakeSessionStore()
    svc = PlanService(session_store=store)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id

    long_run = svc.get(sid)  # e.g. a worker thread's process()
    svc.discard(sid, actor="olaf", reason="duplicate")  # a human, same pod

    with pytest.raises(StaleSessionError):
        svc._save(long_run)
    assert svc.get(sid).status == "discarded"


def test_successive_writes_of_one_copy_advance_the_version():
    store = FakeSessionStore()
    svc = PlanService(session_store=store)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    session = svc.get(sid)
    before = store.rows[sid][1]

    svc._save(session)
    svc._save(session)

    assert store.rows[sid][1] == before + 2


def test_a_session_listed_without_a_version_learns_it_before_writing():
    store = FakeSessionStore()
    a = PlanService(session_store=store)
    sid = a.ingest_text(_PLAN, title="Refund API").session_id
    b = PlanService(session_store=store)
    listed = b._all_sessions()[0]
    before = store.rows[sid][1]

    b._save(listed)

    assert store.rows[sid][1] == before + 1


def test_other_store_errors_are_still_swallowed(caplog):
    store = FakeSessionStore()
    svc = PlanService(session_store=store)
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    store.upsert_error = ConnectionError("db down")

    with caplog.at_level(logging.WARNING, logger="plan.service"):
        svc._save(svc._sessions[sid])  # must not raise

    assert any("shared plan-session write failed" in r.getMessage() for r in caplog.records)


def test_import_skips_a_row_that_already_exists(tmp_path, caplog):
    disk = PlanService(store_dir=tmp_path, persist=True)
    sid = disk.ingest_text(_PLAN, title="Refund API").session_id
    store = FakeSessionStore()
    store.rows[sid] = ('{"advanced": "elsewhere"}', 7)
    # session_ids() raced: another replica inserted after the listing.
    store.session_ids = set  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="plan.service"):
        PlanService(store_dir=tmp_path, persist=True, session_store=store)

    assert store.rows[sid] == ('{"advanced": "elsewhere"}', 7)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and sid in r.getMessage()]
