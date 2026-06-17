"""Tests for the env-buildable readiness check (local-cluster feasibility).

The check is inert by default: with no probe result injected it reports
not_applicable, so it can never block a plan that was never probed.
"""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import NormalizedPlan
from plan.review.readiness.checks import run_readiness


def _plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="050-deploy", title="Deploy the widget to the local cluster",
        source_format="markdown",
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="050-deploy", epic_title="Deploy",
        children=[ChildIssue(key="C1", title="ship it")],
    )


def _result(report):
    return report.result("env-buildable")


def test_env_buildable_not_applicable_without_probe() -> None:
    r = _result(run_readiness(_plan(), _epic()))
    assert r.status == "not_applicable"
    # inert default: hard+waivable but never a blocking fail when no probe ran
    assert r.hard and r.waivable


def test_env_buildable_pass_when_probe_is_buildable() -> None:
    probe = {
        "context": "factory", "buildable": True, "error": None,
        "checks": [
            {"id": "cluster-reachable", "ok": True, "detail": "API server reachable"},
            {"id": "namespace-exists", "ok": True, "detail": "namespace 'factory' present"},
            {"id": "schedulable-nodes", "ok": True, "detail": "2 Ready schedulable node(s)"},
            {"id": "can-schedule-pod", "ok": True, "detail": "may create pods"},
        ],
    }
    r = _result(run_readiness(_plan(), _epic(), local_cluster=probe))
    assert r.status == "pass"
    assert r.severity == "info"


def test_env_buildable_fail_lists_failed_checks() -> None:
    probe = {
        "context": "ghost", "buildable": False, "error": None,
        "checks": [
            {"id": "cluster-reachable", "ok": True, "detail": "API server reachable"},
            {"id": "namespace-exists", "ok": False, "detail": "namespace 'ghost' absent"},
            {"id": "schedulable-nodes", "ok": True, "detail": "2 Ready schedulable node(s)"},
            {"id": "can-schedule-pod", "ok": False, "detail": "cannot create pods (RBAC)"},
        ],
    }
    r = _result(run_readiness(_plan(), _epic(), local_cluster=probe))
    assert r.status == "fail"
    assert r.hard and r.waivable  # blocks emission unless a human waives
    assert "namespace-exists" in r.detail
    assert "can-schedule-pod" in r.detail
    assert r.evidence["probe"]["context"] == "ghost"


def test_env_buildable_fail_falls_back_to_error_when_no_failed_checks() -> None:
    probe = {"context": "dead", "buildable": False, "error": "cluster unreachable", "checks": []}
    r = _result(run_readiness(_plan(), _epic(), local_cluster=probe))
    assert r.status == "fail"
    assert "cluster unreachable" in r.detail
