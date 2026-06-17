"""Local Kubernetes cluster discovery + build/run feasibility probe — read-only.

The cloud discovery primitive (:mod:`agents.cloud.discovery`) answers "can we
reach AWS/GCP/Azure and what's there". This is its **local-cluster** sibling: when
a plan targets an on-prem / local Kubernetes cluster (k3d, kind, minikube, a
shared dev cluster), can we actually *build and run* the proposed workload there?

The planner can derive a cost/effort estimate from text, but whether the cluster
is reachable, the namespace exists, and a pod can be scheduled are *facts about
the environment* — not guessable. This module gathers exactly those facts so the
``env-buildable`` readiness check can turn them into an honest pass/fail instead
of the plan asserting buildability it never verified.

**Authorization model (deliberately conservative).** Every call is read-only and
goes through an injectable ``runner`` seam (default ``subprocess.run``). We only
issue ``config``/``cluster-info``/``get``/``auth can-i`` — never anything that
creates, mutates or deletes. The probe operates on a kubeconfig **context the
operator already configured** (that *is* the scoped, explicit consent — we never
discover or attach credentials ourselves). Nothing here writes to the cluster, so
there is no destructive blast radius to gate; the probe result is advisory input
to planning, recorded as evidence.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable

__all__ = [
    "LocalClusterReadiness",
    "discover_contexts",
    "probe_cluster",
]


@dataclass
class _Cmd:
    """Normalized command result (the subset of CompletedProcess we rely on)."""

    returncode: int
    stdout: str


def _default_runner(argv: list[str]) -> _Cmd:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return _Cmd(returncode=proc.returncode, stdout=proc.stdout or "")


def _run(runner: Callable | None, argv: list[str]) -> _Cmd:
    try:
        r = (runner or _default_runner)(argv)
    except Exception as exc:  # never raise out of a read-only probe
        return _Cmd(returncode=127, stdout=str(exc))
    return _Cmd(
        returncode=getattr(r, "returncode", 1), stdout=getattr(r, "stdout", "") or ""
    )


@dataclass
class LocalClusterReadiness:
    """Outcome of the local-cluster build/run feasibility probe.

    Fields default to the *unknown / not-buildable* side so a probe that could not
    run never reads as a false "yes". ``buildable`` is the single honest verdict:
    reachable AND the namespace exists AND a pod can be scheduled.
    """

    context: str | None = None
    reachable: bool = False
    server: str | None = None
    namespace: str | None = None
    namespace_exists: bool | None = None
    schedulable_nodes: int = 0
    can_schedule_pod: bool | None = None
    error: str | None = None
    checks: list[dict] = field(default_factory=list)

    @property
    def buildable(self) -> bool:
        """True only when every probed prerequisite is satisfied."""
        return bool(
            self.reachable
            and self.namespace_exists
            and self.can_schedule_pod
            and self.schedulable_nodes > 0
        )

    def to_dict(self) -> dict:
        return {
            "context": self.context,
            "reachable": self.reachable,
            "server": self.server,
            "namespace": self.namespace,
            "namespace_exists": self.namespace_exists,
            "schedulable_nodes": self.schedulable_nodes,
            "can_schedule_pod": self.can_schedule_pod,
            "buildable": self.buildable,
            "error": self.error,
            "checks": self.checks,
        }


# ── discovery ──────────────────────────────────────────────────────────────


def discover_contexts(*, runner: Callable | None = None) -> dict:
    """List the kubeconfig contexts available to the operator (read-only).

    Returns ``{"contexts": [...], "current": str|None, "error": str|None}``. Never
    raises — an absent/empty kubeconfig yields an empty list, which downstream
    reads as "no local cluster to build against" (info, not a blocker).
    """
    listing = _run(runner, ["kubectl", "config", "get-contexts", "-o", "name"])
    if listing.returncode != 0:
        return {"contexts": [], "current": None, "error": "kubectl config unavailable"}
    contexts = [ln.strip() for ln in listing.stdout.splitlines() if ln.strip()]
    cur = _run(runner, ["kubectl", "config", "current-context"])
    current = cur.stdout.strip() or None if cur.returncode == 0 else None
    return {"contexts": contexts, "current": current, "error": None}


# ── feasibility probe ────────────────────────────────────────────────────────


def _ctx_args(context: str | None) -> list[str]:
    return ["--context", context] if context else []


def _count_schedulable_nodes(payload: str) -> int:
    """Count Ready, schedulable nodes from ``kubectl get nodes -o json``."""
    try:
        nodes = json.loads(payload).get("items", [])
    except (json.JSONDecodeError, ValueError, AttributeError):
        return 0
    count = 0
    for node in nodes:
        spec = node.get("spec", {}) or {}
        if spec.get("unschedulable") is True:
            continue
        # A control-plane-only node with a NoSchedule taint can't take workloads.
        taints = spec.get("taints", []) or []
        if any(t.get("effect") == "NoSchedule" for t in taints):
            continue
        conds = (node.get("status", {}) or {}).get("conditions", []) or []
        if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds):
            count += 1
    return count


def probe_cluster(
    *,
    context: str | None = None,
    namespace: str | None = None,
    image: str | None = None,
    runner: Callable | None = None,
) -> LocalClusterReadiness:
    """Probe whether the proposed workload can be built/run on a local cluster.

    Read-only checks, in order (each recorded in ``checks`` for the evidence trail):

    1. ``kubectl cluster-info`` — is the API server reachable on this context?
    2. ``kubectl get namespace <ns>`` — does the target namespace exist?
    3. ``kubectl get nodes`` — are there Ready, schedulable nodes?
    4. ``kubectl auth can-i create pods -n <ns>`` — may this principal schedule a
       pod? (a permission *query*, not a pod creation — still read-only.)

    ``image`` is accepted for forward-compat (a future pull-feasibility check) but
    is not exercised here: verifying a pull without scheduling a pod is unreliable,
    and scheduling one would be a mutation. Never raises; on any failure the
    corresponding field stays on the unknown/false side.
    """
    res = LocalClusterReadiness(context=context, namespace=namespace)
    ca = _ctx_args(context)

    info = _run(runner, ["kubectl", "cluster-info", *ca])
    res.reachable = info.returncode == 0
    res.checks.append(
        {
            "id": "cluster-reachable",
            "ok": res.reachable,
            "detail": "API server reachable"
            if res.reachable
            else "cluster unreachable",
        }
    )
    if not res.reachable:
        res.error = "cluster unreachable"
        return res

    # Server URL (best-effort, for the evidence record).
    srv = _run(
        runner,
        [
            "kubectl",
            "config",
            "view",
            "--minify",
            "-o",
            "jsonpath={.clusters[0].cluster.server}",
            *ca,
        ],
    )
    res.server = srv.stdout.strip() or None if srv.returncode == 0 else None

    if namespace:
        ns = _run(runner, ["kubectl", "get", "namespace", namespace, "-o", "name", *ca])
        res.namespace_exists = ns.returncode == 0
        res.checks.append(
            {
                "id": "namespace-exists",
                "ok": res.namespace_exists,
                "detail": f"namespace {namespace!r} "
                + ("present" if res.namespace_exists else "absent"),
            }
        )

    nodes = _run(runner, ["kubectl", "get", "nodes", "-o", "json", *ca])
    res.schedulable_nodes = (
        _count_schedulable_nodes(nodes.stdout) if nodes.returncode == 0 else 0
    )
    res.checks.append(
        {
            "id": "schedulable-nodes",
            "ok": res.schedulable_nodes > 0,
            "detail": f"{res.schedulable_nodes} Ready schedulable node(s)",
        }
    )

    cani = _run(
        runner,
        [
            "kubectl",
            "auth",
            "can-i",
            "create",
            "pods",
            *(["-n", namespace] if namespace else []),
            *ca,
        ],
    )
    can = cani.returncode == 0 and cani.stdout.strip().lower().startswith("yes")
    res.can_schedule_pod = can
    res.checks.append(
        {
            "id": "can-schedule-pod",
            "ok": can,
            "detail": "may create pods" if can else "cannot create pods (RBAC)",
        }
    )
    return res
