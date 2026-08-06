"""Stale readiness verdicts recompute instead of blocking forever (#450).

A readiness verdict was computed once and stored. Fixing a check therefore never
unblocked the sessions it had wrongly failed: the plan stayed unapprovable on a
defect that no longer existed in the code, and the only remedies were to re-plan
(expensive, and it discards review state) or to waive (which records a human
accepting a risk that was never real).

The live case was session ``029-money-safe-vat-quote-endpoint-...``: the word
"untrusted" in a security criterion matched a bare ``rust`` needle, hard-failing
``language-reconciled`` on a Python repo. #397 fixed the detector; the stored
verdict kept failing.

The control that must never move: a plan that genuinely conflicts still fails.
Recomputation is not amnesty.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.recon.models import RepoMap  # noqa: E402
from plan.review.approval import ApprovalError  # noqa: E402
from plan.review.models import PlanReview  # noqa: E402
from plan.review.readiness import revision  # noqa: E402
from plan.review.readiness.checks import run_readiness  # noqa: E402
from plan.review.readiness.models import (  # noqa: E402
    ReadinessCheckResult,
    ReadinessReport,
    Waiver,
)
from plan.review.readiness.revision import gate_revision  # noqa: E402
from plan.review.readiness.waiver import WaiverError  # noqa: E402
from plan.service import PlanService  # noqa: E402

# The 029 shape: a Python/FastAPI spec whose only "rust" is inside "untrusted".
_PYTHON_PLAN = """# VAT quote endpoint
A FastAPI endpoint that quotes VAT.
## Acceptance Criteria
- rejects a quantity above 1000000 with HTTP 422, so an untrusted caller cannot
  trigger unbounded computation
"""

# The control: a spec that really does ask for a different language.
_RUST_PLAN = """# VAT quote endpoint
Rewrite the quote endpoint using cargo and tokio.
## Acceptance Criteria
- the service is built with rust and passes cargo test
"""


def _fail(check_id: str, **kw) -> ReadinessCheckResult:
    return ReadinessCheckResult(
        check_id=check_id,
        title=check_id,
        status="fail",
        severity="high",
        hard=True,
        waivable=True,
        **kw,
    )


def _stale_language_report(plan_id: str, plan_hash: str) -> ReadinessReport:
    """The frozen verdict as session 029 actually carries it.

    Note the evidence has no ``spec_language_signal`` key: the current fail branch
    always writes it (#397 added it), so its absence dates the verdict to a
    pre-fix build. And no ``gate_revision``, because the field did not exist.
    """
    return ReadinessReport(
        plan_id=plan_id,
        plan_hash=plan_hash,
        results=[
            _fail(
                "language-reconciled",
                evidence={
                    "spec_language": "rust",
                    "repo_language": "python",
                    "change_mode": "modify",
                },
            )
        ],
    )


def _session(svc: PlanService, text: str, *, stale: bool) -> str:
    """A processed-looking session on a Python repo, with gates passed."""
    session = svc.ingest_text(text, title="VAT quote endpoint")
    session.plan = session.plan.model_copy(
        update={
            "change_mode": "modify",
            "repo_map": RepoMap(
                available=True,
                repo="acme/billing",
                commit="deadbeefcafe",
                languages=["python"],
            ),
        }
    )
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="VAT quote endpoint",
        children=[
            ChildIssue(
                key="C1",
                title="quote endpoint",
                acceptance_criteria=[c.text for c in session.plan.criteria],
            )
        ],
    )
    review = PlanReview(plan_id=session.plan.plan_id, gates_passed=True)
    review.readiness = (
        _stale_language_report(session.plan.plan_id, session.plan.compute_hash())
        if stale
        else run_readiness(session.plan, session.epic)
    )
    session.review = review
    return session.session_id


# ── what the revision fingerprints ────────────────────────────────────────


def test_gate_revision_covers_the_module_the_397_fix_actually_lived_in(monkeypatch) -> None:
    """#397 was fixed in ``plan.recon.language_reconcile``, not in the check.

    A fingerprint that only covers ``checks.py`` would have called every stale
    verdict fresh, and this bug would still be open.
    """
    real = revision._source_bytes
    revision.gate_revision.cache_clear()
    before = revision.gate_revision()
    monkeypatch.setattr(
        revision,
        "_source_bytes",
        lambda mod: b"changed" if mod == "plan.recon.language_reconcile" else real(mod),
    )
    revision.gate_revision.cache_clear()
    after = revision.gate_revision()
    revision.gate_revision.cache_clear()
    assert after != before


def test_every_fingerprinted_module_resolves() -> None:
    """A renamed or mistyped module would hash a constant marker forever."""
    unreadable = [
        m for m in revision._SOURCE_MODULES if revision._source_bytes(m) == b"<unreadable>"
    ]
    assert unreadable == []


# ── the stamp + the staleness test ────────────────────────────────────────


def test_fresh_report_is_stamped_and_not_stale() -> None:
    svc = PlanService()
    sid = _session(svc, _PYTHON_PLAN, stale=False)
    report = svc.get(sid).review.readiness
    assert report.gate_revision == gate_revision()
    assert report.stale is False
    assert report.recomputed_at == ""  # never recomputed: exactly as first computed


def test_report_from_an_older_build_is_stale() -> None:
    report = ReadinessReport(plan_id="029", gate_revision="")
    assert report.stale is True
    assert ReadinessReport(plan_id="029", gate_revision="deadbeef").stale is True


def test_stale_flag_is_serialised_so_a_reader_can_see_it() -> None:
    """The API/UI must be able to tell a fresh verdict from a stored one."""
    dumped = ReadinessReport(plan_id="029").model_dump()
    assert dumped["stale"] is True
    assert dumped["gate_revision"] == ""
    assert "recomputed_at" in dumped


# ── the merge: refresh clears, but never grants amnesty ───────────────────


def test_refreshed_clears_a_failure_the_recompute_passes() -> None:
    stored = ReadinessReport(plan_id="p", results=[_fail("language-reconciled")])
    fresh = ReadinessReport(
        plan_id="p",
        gate_revision=gate_revision(),
        results=[ReadinessCheckResult(check_id="language-reconciled", title="x", status="pass")],
    )
    out = stored.refreshed(fresh)
    assert out.result("language-reconciled").status == "pass"
    assert out.is_ready()
    assert out.recomputed_at  # and it says when


def test_refreshed_keeps_a_failure_the_recompute_still_finds() -> None:
    stored = ReadinessReport(plan_id="p", results=[_fail("language-reconciled")])
    fresh = ReadinessReport(
        plan_id="p", gate_revision=gate_revision(), results=[_fail("language-reconciled")]
    )
    out = stored.refreshed(fresh)
    assert out.unwaived_hard_failures()


def test_refreshed_will_not_downgrade_a_failure_it_could_not_evaluate() -> None:
    """not_applicable is "unknown", not "fine" — the stored failure stands."""
    stored = ReadinessReport(plan_id="p", results=[_fail("env-buildable")])
    fresh = ReadinessReport(
        plan_id="p",
        gate_revision=gate_revision(),
        results=[
            ReadinessCheckResult(
                check_id="env-buildable", title="x", status="not_applicable", hard=True
            )
        ],
    )
    out = stored.refreshed(fresh)
    kept = out.result("env-buildable")
    assert kept.status == "fail"
    # and it is honest about why it was kept rather than recomputed clean.
    assert kept.evidence["recompute_status"] == "not_applicable"


def test_refreshed_carries_waivers_over() -> None:
    stored = ReadinessReport(plan_id="p", results=[_fail("a")])
    stored.waivers.append(
        Waiver(check_ids=["a"], reason="accepted", waived_by="olaf", plan_hash="h")
    )
    fresh = ReadinessReport(plan_id="p", gate_revision=gate_revision(), results=[_fail("a")])
    out = stored.refreshed(fresh)
    assert [w.check_ids for w in out.waivers] == [["a"]]


# ── the live flow: 029 approves, the control still blocks ─────────────────


def test_approve_recomputes_a_stale_false_positive_and_succeeds() -> None:
    """Session 029: no waiver, no re-plan — the fixed check simply re-runs."""
    svc = PlanService()
    sid = _session(svc, _PYTHON_PLAN, stale=True)
    assert svc.get(sid).review.readiness.stale is True

    out = svc.approve(sid, approver="olaf")

    assert out.status == "approved"
    assert out.review.readiness.result("language-reconciled").status == "pass"
    assert out.review.readiness.stale is False
    assert out.review.readiness.recomputed_at  # honest: recomputed, not stored
    assert out.review.readiness.waivers == []  # nothing was waived to get here


def test_approve_still_blocks_a_real_language_conflict() -> None:
    """The control. Recomputation is not amnesty."""
    svc = PlanService()
    sid = _session(svc, _RUST_PLAN, stale=True)
    with pytest.raises(ApprovalError, match="language-reconciled"):
        svc.approve(sid, approver="olaf")
    # and the recomputed verdict is stored, so the block is current, not frozen.
    report = svc.get(sid).review.readiness
    assert report.stale is False
    assert report.result("language-reconciled").status == "fail"
    assert report.result("language-reconciled").evidence["spec_language_signal"]


def test_re_gate_recomputes_without_approving_or_re_planning() -> None:
    svc = PlanService()
    sid = _session(svc, _PYTHON_PLAN, stale=True)
    out = svc.re_gate(sid)
    assert out.status != "approved"
    assert out.review.readiness.is_ready(out.plan)
    assert out.review.readiness.recomputed_at


def test_waive_refuses_a_stale_failure() -> None:
    """A waiver asserts a human accepted a real risk; a stale fail is not one."""
    svc = PlanService()
    sid = _session(svc, _PYTHON_PLAN, stale=True)
    with pytest.raises(WaiverError, match="not a hard failure"):
        svc.waive(
            sid, check_ids=["language-reconciled"], reason="not really rust", waived_by="olaf"
        )


def test_waive_still_works_on_a_real_failure() -> None:
    svc = PlanService()
    sid = _session(svc, _RUST_PLAN, stale=True)
    out = svc.waive(
        sid, check_ids=["language-reconciled"], reason="deliberate rewrite", waived_by="olaf"
    )
    assert out.review.readiness.is_ready(out.plan)
