"""Tests for the local-cluster discovery + build/run feasibility probe.

Backend-pure: the kubectl runner is injected with canned output — no real
cluster, no network, no mutations. Mirrors tests/test_cloud_discovery.py.
"""

from __future__ import annotations

import json

from agents.cloud.local_cluster import (
    LocalClusterReadiness,
    discover_contexts,
    probe_cluster,
)


def _cmd(returncode: int, stdout: str):
    return type("C", (), {"returncode": returncode, "stdout": stdout})()


def _nodes_json(*, ready=1, unschedulable=0, control_plane=0) -> str:
    items = []
    for _ in range(ready):
        items.append({
            "spec": {},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        })
    for _ in range(unschedulable):
        items.append({
            "spec": {"unschedulable": True},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        })
    for _ in range(control_plane):
        items.append({
            "spec": {"taints": [{"effect": "NoSchedule", "key": "node-role/cp"}]},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        })
    return json.dumps({"items": items})


def _runner(
    *,
    reachable=True,
    ns_exists=True,
    nodes=None,
    can_i="yes",
    fail=None,
):
    """A kubectl runner answering read-only calls from canned data."""
    fail = fail or set()
    nodes = nodes if nodes is not None else _nodes_json(ready=2)

    def run(argv):
        joined = " ".join(argv)
        if any(f in joined for f in fail):
            return _cmd(1, "")
        if "get-contexts" in joined:
            return _cmd(0, "factory\nk3d-dev\nminikube\n")
        if "current-context" in joined:
            return _cmd(0, "factory\n")
        if "cluster-info" in joined:
            return _cmd(0 if reachable else 1, "" if not reachable else "Kubernetes control plane is running")
        if "config view" in joined:
            return _cmd(0, "https://10.0.0.1:6443")
        if "get namespace" in joined:
            return _cmd(0 if ns_exists else 1, "namespace/factory" if ns_exists else "")
        if "get nodes" in joined:
            return _cmd(0, nodes)
        if "auth can-i" in joined:
            return _cmd(0 if can_i == "yes" else 1, can_i)
        return _cmd(0, "")

    return run


# ── discover_contexts ─────────────────────────────────────────────────────


def test_discover_contexts_lists_and_current() -> None:
    out = discover_contexts(runner=_runner())
    assert out["contexts"] == ["factory", "k3d-dev", "minikube"]
    assert out["current"] == "factory"
    assert out["error"] is None


def test_discover_contexts_no_kubeconfig_is_empty_not_error() -> None:
    def run(argv):
        return _cmd(1, "")  # kubectl present but no config

    out = discover_contexts(runner=run)
    assert out["contexts"] == []
    assert out["error"] == "kubectl config unavailable"


# ── probe_cluster ─────────────────────────────────────────────────────────


def test_probe_buildable_when_everything_passes() -> None:
    res = probe_cluster(context="factory", namespace="factory", runner=_runner())
    assert res.reachable is True
    assert res.namespace_exists is True
    assert res.schedulable_nodes == 2
    assert res.can_schedule_pod is True
    assert res.buildable is True
    assert res.server == "https://10.0.0.1:6443"
    # every check recorded for the evidence trail
    assert {c["id"] for c in res.checks} == {
        "cluster-reachable", "namespace-exists", "schedulable-nodes", "can-schedule-pod",
    }


def test_probe_unreachable_short_circuits() -> None:
    res = probe_cluster(context="dead", namespace="factory", runner=_runner(reachable=False))
    assert res.reachable is False
    assert res.buildable is False
    assert res.error == "cluster unreachable"
    # short-circuits before namespace/node checks
    assert [c["id"] for c in res.checks] == ["cluster-reachable"]


def test_probe_missing_namespace_is_not_buildable() -> None:
    res = probe_cluster(context="factory", namespace="ghost", runner=_runner(ns_exists=False))
    assert res.reachable is True
    assert res.namespace_exists is False
    assert res.buildable is False


def test_probe_no_schedulable_nodes_is_not_buildable() -> None:
    # only a tainted control-plane node + an unschedulable node → 0 schedulable
    runner = _runner(nodes=_nodes_json(ready=0, unschedulable=1, control_plane=1))
    res = probe_cluster(context="factory", namespace="factory", runner=runner)
    assert res.schedulable_nodes == 0
    assert res.buildable is False


def test_probe_rbac_denied_pod_create_is_not_buildable() -> None:
    res = probe_cluster(context="factory", namespace="factory", runner=_runner(can_i="no"))
    assert res.can_schedule_pod is False
    assert res.buildable is False


def test_probe_never_raises_on_runner_exception() -> None:
    def boom(argv):
        raise RuntimeError("kubectl exploded")

    res = probe_cluster(context="factory", namespace="factory", runner=boom)
    assert isinstance(res, LocalClusterReadiness)
    assert res.reachable is False
    assert res.buildable is False


def test_to_dict_is_json_serializable_and_carries_verdict() -> None:
    res = probe_cluster(context="factory", namespace="factory", runner=_runner())
    d = res.to_dict()
    json.dumps(d)  # must not raise
    assert d["buildable"] is True
    assert d["context"] == "factory"
