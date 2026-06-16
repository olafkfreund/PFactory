"""RFC-0007 (#86 PR-b): the curation gate flips curated:true under strict rules."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.access_discovery import curate_access, curate_requirement  # noqa: E402

_OK = lambda _req: True  # noqa: E731
_FAIL = lambda _req: False  # noqa: E731
_APPROVAL = {
    "approved_by": "olaf",
    "approved_at": "2026-06-16",
    "scope": "sandbox acct",
}


def _req(**kw):
    base = {"resource": "web", "auth_class": "B-bootstrap-once", "bootstrap": "human"}
    base.update(kw)
    return base


def test_class_D_is_never_curated():
    out, audit = curate_requirement(
        _req(auth_class="D-un-automatable"), approval=_APPROVAL, liveness_check=_OK
    )
    assert out.get("curated") is not True and audit is None


def test_human_bootstrap_requires_approval():
    out, audit = curate_requirement(_req(), approval=None, liveness_check=_OK)
    assert out.get("curated") is not True and audit is None


def test_liveness_must_pass():
    out, audit = curate_requirement(_req(), approval=_APPROVAL, liveness_check=_FAIL)
    assert out.get("curated") is not True and audit is None
    out2, _ = curate_requirement(_req(), approval=_APPROVAL, liveness_check=None)
    assert out2.get("curated") is not True  # no liveness check -> no curation


def test_success_flips_curated_and_emits_audit():
    out, audit = curate_requirement(
        _req(credential_ref="store:tc_1"), approval=_APPROVAL, liveness_check=_OK
    )
    assert out["curated"] is True
    assert out["human_approval"] == {
        "approved_by": "olaf",
        "approved_at": "2026-06-16",
        "scope": "sandbox acct",
    }
    assert audit["kind"] == "access_curated"
    assert audit["resource"] == "web" and audit["liveness"] == "passed"
    assert audit["approved_by"] == "olaf"
    # the audit carries a ref, never a secret value
    assert audit["credential_ref"] == "store:tc_1"


def test_machine_native_needs_no_approval_just_liveness():
    out, audit = curate_requirement(
        _req(auth_class="A-machine-native", bootstrap="none", credential_ref="env:T"),
        liveness_check=_OK,
    )
    assert out["curated"] is True and audit is not None
    assert "human_approval" not in out  # no human approval needed for class A


def test_idempotent_on_already_curated():
    out, audit = curate_requirement(
        _req(curated=True), approval=_APPROVAL, liveness_check=_OK
    )
    assert out["curated"] is True and audit is None


def test_curate_access_batch_and_all_curated():
    reqs = [
        _req(
            resource="api",
            auth_class="A-machine-native",
            bootstrap="none",
            credential_ref="env:T",
        ),
        _req(resource="web", credential_ref="store:1"),
        _req(resource="mfa", auth_class="D-un-automatable"),
    ]
    res = curate_access(
        reqs,
        approvals={"web": _APPROVAL},  # api needs none; mfa is refused
        liveness_check=_OK,
    )
    states = {r["resource"]: bool(r.get("curated")) for r in res["requirements"]}
    assert states == {"api": True, "web": True, "mfa": False}
    assert res["all_curated"] is False  # mfa (class D) can never curate
    assert {a["resource"] for a in res["audit"]} == {"api", "web"}
