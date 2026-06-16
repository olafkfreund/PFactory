"""RFC-0007 (#86 PR-d): PlanService.approve_access records a human approval."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.service import PlanService, PlanServiceError, PlanSession  # noqa: E402


def _svc(reqs):
    svc = PlanService(persist=False)
    sess = PlanSession.model_construct(
        session_id="s1",
        contract_result={"contract": {"access": {"requirements": reqs}}},
        access_approvals={},
        access_audit=[],
    )
    svc._sessions["s1"] = sess
    return svc, sess


def test_approve_curates_present_credential():
    reqs = [
        {
            "resource": "web",
            "auth_class": "B-bootstrap-once",
            "bootstrap": "human",
            "credential_ref": "store:tc_1",
        }
    ]
    svc, sess = _svc(reqs)
    res = svc.approve_access(
        "s1", "web", approved_by="olaf", scope="staging", ref_exists=lambda r: True
    )
    assert res["ok"] is True and res["state"] == "curated"
    assert sess.access_approvals["web"]["approved_by"] == "olaf"
    assert sess.access_approvals["web"]["scope"] == "staging"
    assert len(sess.access_audit) == 1
    assert sess.access_audit[0]["kind"] == "access_curated"
    assert (
        sess.access_audit[0]["credential_ref"] == "store:tc_1"
    )  # a ref, never a secret


def test_refused_when_credential_not_present():
    reqs = [
        {
            "resource": "web",
            "auth_class": "B-bootstrap-once",
            "bootstrap": "human",
            "credential_ref": "store:x",
        }
    ]
    svc, sess = _svc(reqs)
    res = svc.approve_access(
        "s1", "web", approved_by="o", scope="s", ref_exists=lambda r: None
    )
    assert res["ok"] is False
    assert sess.access_approvals == {} and sess.access_audit == []


def test_refused_for_class_D():
    reqs = [{"resource": "mfa", "auth_class": "D-un-automatable", "bootstrap": "human"}]
    svc, sess = _svc(reqs)
    res = svc.approve_access(
        "s1", "mfa", approved_by="o", scope="s", ref_exists=lambda r: True
    )
    assert res["ok"] is False and sess.access_approvals == {}


def test_unknown_resource_raises():
    svc, _ = _svc([])
    with pytest.raises(PlanServiceError):
        svc.approve_access("s1", "nope", approved_by="o", scope="s")
