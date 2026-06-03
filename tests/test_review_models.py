"""Tests for the review/synthesize data contracts (#13–#17 foundations)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue  # noqa: E402
from plan.review.models import (  # noqa: E402
    Finding,
    HumanApproval,
    LensScore,
    PlanReview,
)
from plan.synthesize.models import SynthesizedArtifact  # noqa: E402


def _review(scores, *, threshold=0.75, blocking_lens=None):
    lenses = []
    for name, s in scores.items():
        findings = []
        if blocking_lens == name:
            findings = [Finding(title="bad", severity="critical", blocking=True)]
        lenses.append(LensScore(lens=name, score=s, findings=findings))
    return PlanReview(plan_id="001-x", lenses=lenses, threshold=threshold).recompute()


def test_gates_pass_when_all_lenses_above_threshold():
    r = _review({"architecture": 0.9, "security": 0.8, "feasibility": 0.76})
    assert r.gates_passed is True
    assert r.aggregate_score == pytest.approx(0.82, abs=0.01)


def test_gates_fail_when_a_lens_below_threshold():
    r = _review({"architecture": 0.9, "security": 0.5})
    assert r.gates_passed is False


def test_blocking_finding_fails_gate_even_if_scores_pass():
    r = _review({"architecture": 0.9, "security": 0.9}, blocking_lens="security")
    assert r.gates_passed is False
    assert len(r.blocking_findings()) == 1


def test_ready_to_emit_requires_gates_and_valid_approval():
    r = _review({"architecture": 0.9, "security": 0.9})
    assert r.ready_to_emit() is False  # no approval yet
    r.human_approval = HumanApproval(approved=True, valid=True, approved_by="olaf")
    assert r.ready_to_emit() is True


def test_synth_artifact_carries_doc_and_child():
    art = SynthesizedArtifact(
        kind="testing",
        title="Testing Strategy",
        document="# Testing Strategy\n...",
        child=ChildIssue(key="T1", title="Set up testing", kind="testing"),
    )
    assert art.child.kind == "testing"
    assert art.document.startswith("# Testing Strategy")
