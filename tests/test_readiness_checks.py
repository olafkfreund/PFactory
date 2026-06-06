"""Tests for the readiness check catalog (epic #33, P0.2 / P0.4)."""

from __future__ import annotations

from plan.decompose.models import AccessRequirement, ChildIssue, EpicPlan
from plan.models import Criterion, Enrichment, NormalizedPlan
from plan.review.models import Finding
from plan.review.readiness.checks import run_readiness


def _plan(
    *,
    title: str = "Build the widget service",
    criteria: list[tuple[str, str]] | None = None,
    infra: list[dict] | None = None,
    plan_type: str | None = None,
) -> NormalizedPlan:
    crit = [Criterion(id=i, text=t) for i, t in (criteria or [])]
    return NormalizedPlan(
        plan_id="001-widget",
        title=title,
        source_format="markdown",
        criteria=crit,
        plan_type=plan_type,
        enrichment=Enrichment(infra=infra or []),
    ).with_hash()


def _epic(children: list[ChildIssue] | None = None, **kw) -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget",
        epic_title="Widget",
        children=children if children is not None else [],
        **kw,
    )


def _status(report, check_id: str) -> str:
    return report.result(check_id).status


# ── children-present ──────────────────────────────────────────────────────


def test_children_present_pass_and_fail() -> None:
    epic = _epic([ChildIssue(key="C1", title="x")])
    assert _status(run_readiness(_plan(), epic), "children-present") == "pass"
    assert _status(run_readiness(_plan(), _epic([])), "children-present") == "fail"
    # non-waivable
    r = run_readiness(_plan(), _epic([])).result("children-present")
    assert r.hard and not r.waivable


# ── criteria-present ──────────────────────────────────────────────────────


def test_criteria_present_detects_empty() -> None:
    with_crit = _plan(criteria=[("AC#1", "user can log in")])
    assert _status(run_readiness(with_crit, _epic()), "criteria-present") == "pass"
    assert _status(run_readiness(_plan(), _epic()), "criteria-present") == "fail"


# ── ac-child-coverage ─────────────────────────────────────────────────────


def test_ac_child_coverage_full_partial_na() -> None:
    plan = _plan(criteria=[("AC#1", "user can log in"), ("AC#2", "user can log out")])
    covered = _epic(
        [
            ChildIssue(key="C1", title="login", acceptance_criteria=["user can log in"]),
            ChildIssue(key="C2", title="logout", acceptance_criteria=["user can log out"]),
        ]
    )
    assert _status(run_readiness(plan, covered), "ac-child-coverage") == "pass"

    partial = _epic([ChildIssue(key="C1", title="login", acceptance_criteria=["user can log in"])])
    rep = run_readiness(plan, partial)
    assert _status(rep, "ac-child-coverage") == "fail"
    assert rep.result("ac-child-coverage").evidence["uncovered_acs"] == ["AC#2"]

    # No criteria → not applicable (criteria-present owns that failure)
    assert _status(run_readiness(_plan(), _epic()), "ac-child-coverage") == "not_applicable"


# ── deps-sound ────────────────────────────────────────────────────────────


def test_deps_sound_pass_dangling_cycle() -> None:
    ok = _epic(
        [ChildIssue(key="C1", title="a"), ChildIssue(key="C2", title="b", depends_on=["C1"])]
    )
    assert _status(run_readiness(_plan(), ok), "deps-sound") == "pass"

    dangling = _epic([ChildIssue(key="C1", title="a", depends_on=["C9"])])
    rd = run_readiness(_plan(), dangling).result("deps-sound")
    assert rd.status == "fail" and rd.waivable  # dangling is waivable

    cycle = _epic(
        [
            ChildIssue(key="C1", title="a", depends_on=["C2"]),
            ChildIssue(key="C2", title="b", depends_on=["C1"]),
        ]
    )
    rc = run_readiness(_plan(), cycle).result("deps-sound")
    assert rc.status == "fail" and not rc.waivable  # cycle is NOT waivable


# ── access-granted ────────────────────────────────────────────────────────


def test_access_granted_denied_pass_na() -> None:
    denied = _epic(
        access_requirements=[
            AccessRequirement(provider="aws", action="eks:CreateCluster", granted=False)
        ]
    )
    assert _status(run_readiness(_plan(), denied), "access-granted") == "fail"

    granted = _epic(
        access_requirements=[
            AccessRequirement(provider="aws", action="s3:PutObject", granted=True)
        ]
    )
    assert _status(run_readiness(_plan(), granted), "access-granted") == "pass"

    # no requirements → not applicable
    assert _status(run_readiness(_plan(), _epic()), "access-granted") == "not_applicable"


def test_access_granted_scans_child_requirements() -> None:
    epic = _epic(
        [
            ChildIssue(
                key="C1",
                title="infra",
                access_requirements=[
                    AccessRequirement(provider="aws", action="rds:CreateDBInstance", granted=False)
                ],
            )
        ]
    )
    assert _status(run_readiness(_plan(), epic), "access-granted") == "fail"


# ── enrichment-integrity ──────────────────────────────────────────────────


def test_enrichment_integrity_states() -> None:
    cloud = _plan(title="Deploy the service to AWS EKS", criteria=[("AC#1", "runs on eks")])
    noncloud = _plan(title="Write a blog post", criteria=[("AC#1", "publish post")])

    # empty infra → not applicable (air-gapped / adapters off)
    assert _status(run_readiness(cloud, _epic()), "enrichment-integrity") == "not_applicable"

    # adapter ran ok → pass
    ok_infra = _plan(title="Deploy to EKS", infra=[{"adapter": "aws", "available": True}])
    assert _status(run_readiness(ok_infra, _epic()), "enrichment-integrity") == "pass"

    # adapter failed on a cloud plan → fail (the gap-5 fix)
    failed_cloud = _plan(
        title="Deploy to EKS", infra=[{"adapter": "aws", "available": False, "error": "no creds"}]
    )
    rep = run_readiness(failed_cloud, _epic())
    assert _status(rep, "enrichment-integrity") == "fail"
    assert rep.result("enrichment-integrity").evidence["failed_adapters"] == ["aws"]

    # adapter failed but plan not cloud-relevant → not applicable
    failed_noncloud = NormalizedPlan(
        plan_id="x", title="Write a blog post", source_format="markdown",
        enrichment=Enrichment(infra=[{"adapter": "aws", "available": False}]),
    ).with_hash()
    assert _status(run_readiness(failed_noncloud, _epic()), "enrichment-integrity") == "not_applicable"
    assert noncloud  # silence unused


# ── no-blocking-findings ──────────────────────────────────────────────────


def test_no_blocking_findings() -> None:
    epic = _epic([ChildIssue(key="C1", title="x")])
    assert _status(run_readiness(_plan(), epic), "no-blocking-findings") == "pass"

    blockers = [Finding(title="Hardcoded secret", severity="critical", blocking=True)]
    rep = run_readiness(_plan(), epic, blocking_findings=blockers)
    r = rep.result("no-blocking-findings")
    assert r.status == "fail" and r.hard and not r.waivable
