"""Tests for the multi-lens review gates + rules engine (issues #15–#16)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, Enrichment, NormalizedPlan  # noqa: E402
from plan.review.gates import run_gates  # noqa: E402
from plan.review.models import Finding  # noqa: E402
from plan.review.rules.engine import run_external_policy, run_rules  # noqa: E402


def _plan(
    *,
    target_kind="software",
    plan_type=None,
    description="Build a service",
    criteria=None,
    raw_text=None,
    infra=None,
    knowledge=None,
):
    crits = criteria if criteria is not None else [
        Criterion(id="AC#1", text="Users can log in with OAuth"),
        Criterion(id="AC#2", text="API returns JSON"),
    ]
    return NormalizedPlan(
        plan_id="001-svc",
        title="Build a payments service",
        description=description,
        source_format="markdown",
        target_kind=target_kind,
        plan_type=plan_type,
        criteria=crits,
        raw_text=raw_text,
        enrichment=Enrichment(infra=infra or [], knowledge=knowledge or []),
    )


def _epic(children):
    return EpicPlan(
        plan_id="001-svc",
        epic_title="Build a payments service",
        epic_body="Stand up the payments service end to end.",
        children=children,
        summary="payments service",
    )


def _healthy_children():
    return [
        # A complete service feature now carries the implicit runtime ACs the
        # completeness lens enforces (RFC-0008 §3.1, #166): boots, declares
        # dependencies, health check, deployable. In the live pipeline these are
        # auto-injected before gates; the fixture states them explicitly.
        ChildIssue(
            key="C1",
            title="Implement payments API",
            kind="feature",
            acceptance_criteria=[
                "The service starts and serves traffic without error",
                "Declares its dependencies in requirements.txt",
                "Exposes a /health endpoint returning 200",
                "Deployable as a container image",
            ],
        ),
        ChildIssue(
            key="C2", title="Add auth middleware", kind="feature", depends_on=["C1"]
        ),
        ChildIssue(
            key="C3", title="Write integration tests", kind="testing", depends_on=["C1"]
        ),
        ChildIssue(
            key="C4", title="Set up CI/CD pipeline", kind="cicd", depends_on=["C1"]
        ),
    ]


def test_healthy_software_plan_passes():
    review = run_gates(_plan(), _epic(_healthy_children()))
    assert review.code_gates_applied is True
    assert review.gates_passed is True
    assert not review.blocking_findings()


def test_hardcoded_secret_blocks():
    plan = _plan(description="set API_KEY=supersecretvalue123 in the config")
    review = run_gates(plan, _epic(_healthy_children()))
    assert review.gates_passed is False
    blockers = review.blocking_findings()
    assert blockers
    assert any(f.severity == "critical" for f in blockers)


def test_dependency_cycle_fails_feasibility():
    children = [
        ChildIssue(key="C1", title="Step one", kind="feature", depends_on=["C2"]),
        ChildIssue(key="C2", title="Step two", kind="feature", depends_on=["C1"]),
        ChildIssue(key="C3", title="Write tests", kind="testing"),
        ChildIssue(key="C4", title="Set up CI/CD", kind="cicd"),
    ]
    review = run_gates(_plan(), _epic(children))
    feas = next(ls for ls in review.lenses if ls.lens == "feasibility")
    assert feas.findings
    assert review.gates_passed is False


def test_missing_testing_and_cicd_lowers_best_practices():
    children = [
        ChildIssue(key="C1", title="Implement payments API", kind="feature"),
        ChildIssue(
            key="C2", title="Add auth middleware", kind="feature", depends_on=["C1"]
        ),
    ]
    review = run_gates(_plan(), _epic(children))
    bp = next(ls for ls in review.lenses if ls.lens == "best-practices")
    assert bp.score < 0.75
    assert review.gates_passed is False


def test_non_software_plan_skips_code_gates():
    plan = _plan(
        target_kind="non-software",
        description="Write the Q3 marketing campaign brief",
        criteria=[Criterion(id="AC#1", text="Brief approved by stakeholders")],
    )
    children = [
        ChildIssue(key="C1", title="Draft campaign brief", kind="task"),
        ChildIssue(key="C2", title="Review with stakeholders", kind="task"),
    ]
    review = run_gates(plan, _epic(children))
    assert review.code_gates_applied is False
    lens_names = {ls.lens for ls in review.lenses}
    assert "security" not in lens_names
    assert "architecture" not in lens_names
    # Not penalised for missing testing/cicd children.
    bp = next(ls for ls in review.lenses if ls.lens == "best-practices")
    assert bp.score == pytest.approx(1.0)
    assert review.gates_passed is True


def test_run_rules_detects_plaintext_secret():
    plan = _plan(raw_text="password: hunter2hunter2")
    findings = run_rules(plan, _epic(_healthy_children()))
    secret = [f for f in findings if f.source == "no-plaintext-secrets"]
    assert secret and secret[0].blocking and secret[0].severity == "critical"


def test_infra_change_needs_rollback_rule():
    plan = _plan(plan_type="infra-change", description="resize the prod database")
    findings = run_rules(plan, _epic(_healthy_children()))
    assert any(f.source == "infra-change-needs-rollback" for f in findings)

    ok_plan = _plan(
        plan_type="infra-change",
        description="resize the prod database with a documented rollback plan",
    )
    ok_findings = run_rules(ok_plan, _epic(_healthy_children()))
    assert not any(f.source == "infra-change-needs-rollback" for f in ok_findings)


def test_k8s_workload_without_limits_rule():
    plan = _plan(infra=[{"kind": "Deployment", "name": "api", "limits": None}])
    findings = run_rules(plan, _epic(_healthy_children()))
    assert any(f.source == "k8s-workloads-need-limits" for f in findings)


def test_external_policy_seam_is_lazy_and_injectable():
    plan = _plan()
    epic = _epic(_healthy_children())
    # No runner → no-op.
    assert run_external_policy(plan, epic) == []

    def fake_runner(p, e):
        return [Finding(title="OPA violation", severity="high", source="opa")]

    out = run_external_policy(plan, epic, runner=fake_runner)
    assert len(out) == 1 and out[0].source == "opa"

    # Folded into the review through the gate seam.
    review = run_gates(plan, epic, external_runner=fake_runner)
    assert any(f.source == "opa" for ls in review.lenses for f in ls.findings)
