"""Tests for RFC-0011 hard routing in PlanService (issue #182)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.service import PlanService, PlanServiceError, _route_tier  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT
"""


# ── _route_tier (highest wins; migration => hard) ──────────────────────────


def test_route_tier_highest_wins() -> None:
    assert _route_tier("low", is_migration=False) == "low"
    assert _route_tier("medium", is_migration=False) == "medium"
    assert _route_tier("hard", is_migration=False) == "hard"


def test_route_tier_migration_forces_hard() -> None:
    assert _route_tier(None, is_migration=True) == "hard"
    assert _route_tier("low", is_migration=True) == "hard"
    assert _route_tier("medium", is_migration=True) == "hard"


def test_route_tier_none_without_signal() -> None:
    assert _route_tier(None, is_migration=False) is None
    assert _route_tier("bogus", is_migration=False) is None


# ── tier carried from intake through process ───────────────────────────────


def test_carried_tier_survives_process() -> None:
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API", autonomy_tier="medium").session_id
    assert svc.get(sid).plan.autonomy_tier == "medium"
    processed = svc.process(sid)
    assert processed.plan.autonomy_tier == "medium"


def test_intake_normalizes_scoped_tier() -> None:
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API", autonomy_tier="factory:hard").session_id
    assert svc.get(sid).plan.autonomy_tier == "hard"


def test_no_tier_stays_none() -> None:
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API").session_id
    processed = svc.process(sid)
    assert processed.plan.autonomy_tier is None


# ── migration => hard in process (recon mocked) ────────────────────────────


def test_process_forces_hard_for_migration(monkeypatch) -> None:
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API", autonomy_tier="low").session_id

    # Make _reconnoiter report a migration without touching a real repo.
    def _fake_recon(self, session, plan):  # noqa: ANN001
        return plan.model_copy(update={"change_mode": "migration"})

    monkeypatch.setattr(PlanService, "_reconnoiter", _fake_recon)
    processed = svc.process(sid)
    # low was carried, but a rewrite forces hard (highest wins).
    assert processed.plan.change_mode == "migration"
    assert processed.plan.autonomy_tier == "hard"


# ── hard holds the contract emit until approval ────────────────────────────


def test_hard_contract_emit_blocked_without_approval() -> None:
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API", autonomy_tier="hard").session_id
    svc.process(sid)  # status -> processed, not approved
    with pytest.raises(PlanServiceError, match="requires human approval"):
        svc.emit_contract(sid, repo="olafkfreund/Demo", dry_run=False)


def test_hard_contract_dry_run_allowed_without_approval() -> None:
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API", autonomy_tier="hard").session_id
    svc.process(sid)
    # dry-run never creates anything, so it is never gated.
    out = svc.emit_contract(sid, repo="olafkfreund/Demo", dry_run=True)
    assert out.contract_result["dry_run"] is True
    assert out.contract_result["contract"]["execution"]["autonomy_tier"] == "hard"
    assert out.contract_result["contract"]["execution"]["skip_planning"] is False


def test_low_tier_contract_emit_not_gated_by_tier(monkeypatch) -> None:
    # A low tier must NOT trip the hard approval gate. Use an http stub so the
    # live path runs without a real AIFactory.
    svc = PlanService()
    sid = svc.ingest_text(_PLAN, title="Refund API", autonomy_tier="low").session_id
    svc.process(sid)

    class _Resp:
        def json(self):
            return {"taskId": "T1"}

    class _Http:
        def post(self, *a, **k):
            return _Resp()

    out = svc.emit_contract(sid, repo="olafkfreund/Demo", dry_run=False, http=_Http())
    # Not raised by the tier gate; the emit proceeded.
    assert out.contract_result.get("ok") is True
