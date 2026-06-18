"""Tests for the EU AI Act audit-pack export (MVP, #122)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.emit.audit_pack import (  # noqa: E402
    DISCLAIMER,
    build_audit_pack,
    render_markdown,
)
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.review.models import (  # noqa: E402
    Citation,
    Finding,
    HumanApproval,
    LensScore,
    PlanReview,
)
from plan.service import PlanSession  # noqa: E402

_NOW = "2026-06-18T12:00:00+00:00"


def _full_session() -> PlanSession:
    plan = NormalizedPlan(
        plan_id="001-taskboard",
        title="Task board service",
        description="A kanban task board REST API.",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="A task can be created")],
    ).with_hash()
    review = PlanReview(
        plan_id="001-taskboard",
        aggregate_score=0.92,
        gates_passed=True,
        lenses=[
            LensScore(
                lens="architecture",
                score=0.9,
                findings=[
                    Finding(
                        title="Stateless API",
                        detail="Good separation.",
                        severity="info",
                        source="architecture",
                        citations=[
                            Citation(title="12-factor", uri="https://12factor.net",
                                     why="cited norm")
                        ],
                    )
                ],
            )
        ],
        human_approval=HumanApproval(
            approved=True, approved_by="olaf", approved_at=_NOW,
            plan_hash="abc123", valid=True, review_count=1,
        ),
    )
    return PlanSession(
        session_id="001-taskboard",
        status="emitted",
        plan=plan,
        review=review,
        contract_result={"contract": {"correlation_key": "18", "signature": "deadbeef"}},
        correlation_key="18",
        emitted_issue_number=18,
        aifactory_task_id="task-42",
    )


def _minimal_session() -> PlanSession:
    plan = NormalizedPlan(
        plan_id="002-bare",
        title="Bare plan",
        source_format="markdown",
        target_kind="software",
    ).with_hash()
    return PlanSession(session_id="002-bare", plan=plan)


# ── build_audit_pack ─────────────────────────────────────────────────────


def test_pack_has_all_artifacts_and_disclaimer() -> None:
    pack = build_audit_pack(_full_session(), now=_NOW)
    assert pack.disclaimer == DISCLAIMER
    assert pack.generated_at == _NOW
    keys = {a.key for a in pack.artifacts}
    assert keys == {
        "source_document", "review_findings", "human_approval",
        "task_contract", "completion_timeline", "tfactory_verdict",
    }
    # every artifact maps to at least one EU AI Act heading label
    assert all(a.eu_ai_act_headings for a in pack.artifacts)


def test_full_session_presence_flags() -> None:
    pack = build_audit_pack(_full_session(), now=_NOW)
    present = {a.key: a.present for a in pack.artifacts}
    assert present["source_document"] is True
    assert present["review_findings"] is True
    assert present["human_approval"] is True
    assert present["task_contract"] is True
    assert present["completion_timeline"] is True
    # TFactory verdict lives downstream — honestly absent on the PFactory record
    assert present["tfactory_verdict"] is False


def test_pack_is_self_contained_inlines_evidence() -> None:
    pack = build_audit_pack(_full_session(), now=_NOW)
    by_key = {a.key: a for a in pack.artifacts}
    # the source doc inlines criteria + hash; the approval inlines who/when
    assert by_key["source_document"].content["acceptance_criteria"][0]["id"] == "AC#1"
    assert by_key["human_approval"].content["approved_by"] == "olaf"
    assert by_key["task_contract"].content["contract"]["signature"] == "deadbeef"


def test_minimal_session_marks_absent_without_crashing() -> None:
    pack = build_audit_pack(_minimal_session(), now=_NOW)
    present = {a.key: a.present for a in pack.artifacts}
    # only the source document is always present
    assert present["source_document"] is True
    assert present["review_findings"] is False
    assert present["human_approval"] is False
    assert present["task_contract"] is False
    assert present["completion_timeline"] is False


# ── render_markdown ──────────────────────────────────────────────────────


def test_markdown_carries_disclaimer_and_cross_reference() -> None:
    md = render_markdown(build_audit_pack(_full_session(), now=_NOW))
    assert DISCLAIMER in md
    assert "## Cross-reference" in md
    assert "Human oversight (Art. 14)" in md
    assert "Signed Task Contract" in md


def test_markdown_makes_no_compliance_claim() -> None:
    md = render_markdown(build_audit_pack(_full_session(), now=_NOW)).lower()
    # descriptive only — never asserts the plan/system "complies" / "is compliant"
    assert "complies with" not in md
    assert "is compliant" not in md
    assert "certified compliant" not in md
