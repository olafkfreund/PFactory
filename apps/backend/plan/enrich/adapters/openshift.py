"""Read-only OpenShift infrastructure adapter (#9).

Builds on the Kubernetes collection (deployments, statefulsets, daemonsets,
NetworkPolicies, ResourceQuotas) and layers OpenShift-specific reads on top:
Projects, Routes and SecurityContextConstraints (SCCs). The resulting
:class:`~plan.enrich.base.InfraSnapshot` carries SCC summaries in ``policies``
and flags overly-permissive SCC bindings (``anyuid``/``privileged``/``hostaccess``)
in ``findings``.

**Read-only is a hard contract.** Only ``list_*``/``read_*`` reads through the
injected :class:`OpenShiftReader`; never create/update/patch/delete.

Design for testability: the real ``kubernetes``/``openshift`` clients are
*lazily* imported inside :meth:`OpenShiftAdapter._build_reader`, never at module
import. Tests inject a fake reader and need no SDK installed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from plan.enrich.adapters.kubernetes import (
    K8sReader,
    collect_k8s_core,
)
from plan.enrich.base import InfraAdapter, InfraSnapshot, register_adapter

# SCCs that grant elevated/unsafe privileges when bound to workloads.
_PERMISSIVE_SCCS = {"anyuid", "privileged", "hostaccess", "hostnetwork", "hostmount-anyuid"}


@runtime_checkable
class OpenShiftReader(K8sReader, Protocol):
    """Read-only seam over an OpenShift cluster.

    Extends :class:`~plan.enrich.adapters.kubernetes.K8sReader` with the
    OpenShift-specific list reads. All methods perform a single list and return
    plain JSON-able dicts; no mutating operation is exposed.
    """

    def list_projects(self) -> list[dict]: ...

    def list_routes(self) -> list[dict]: ...

    def list_scc(self) -> list[dict]: ...


# ── helpers ─────────────────────────────────────────────────────────────


def summarise_route(item: dict) -> dict:
    """Normalise an OpenShift Route object."""
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "host": spec.get("host", ""),
        "to": (spec.get("to") or {}).get("name", ""),
        "tls": bool(spec.get("tls")),
    }


def summarise_scc(item: dict) -> dict:
    """Normalise a SecurityContextConstraints object."""
    metadata = item.get("metadata") or {}
    name = metadata.get("name", "")
    users = item.get("users") or []
    groups = item.get("groups") or []
    return {
        "name": name,
        "kind": "SecurityContextConstraints",
        "allow_privileged_container": bool(item.get("allowPrivilegedContainer")),
        "allow_host_network": bool(item.get("allowHostNetwork")),
        "allow_host_pid": bool(item.get("allowHostPID")),
        "run_as_user": (item.get("runAsUser") or {}).get("type", ""),
        "users": users,
        "groups": groups,
        "bound": bool(users or groups),
    }


def scc_findings(sccs: list[dict]) -> list[str]:
    """Flag overly-permissive SCCs that are bound to users/groups."""
    findings: list[str] = []
    for scc in sccs:
        name = scc.get("name", "")
        if not scc.get("bound"):
            continue
        if name in _PERMISSIVE_SCCS or scc.get("allow_privileged_container"):
            subjects = (scc.get("users") or []) + (scc.get("groups") or [])
            findings.append(
                f"permissive SCC '{name}' is bound to: {', '.join(subjects) or 'unknown'}"
            )
    return findings


@register_adapter
class OpenShiftAdapter(InfraAdapter):
    """Read-only adapter for a live OpenShift cluster (#9)."""

    name = "openshift"

    def __init__(
        self,
        *,
        target: str = "",
        reader: OpenShiftReader | None = None,
        kubeconfig: str | None = None,
        **options: object,
    ) -> None:
        super().__init__(target=target, **options)
        self._reader = reader
        self.kubeconfig = kubeconfig

    def available(self) -> bool:
        """True if a reader was injected or some kubeconfig/in-cluster auth exists."""
        if self._reader is not None:
            return True
        # Reuse the Kubernetes auth-detection logic (same kubeconfig surface).
        from plan.enrich.adapters.kubernetes import _kubeconfig_present

        return _kubeconfig_present(self.kubeconfig)

    def _build_reader(self) -> OpenShiftReader:
        """Return the injected reader, or build a real one lazily."""
        if self._reader is not None:
            return self._reader
        return _LiveOpenShiftReader(kubeconfig=self.kubeconfig)

    def discover(self) -> InfraSnapshot:
        """Assemble a read-only snapshot of the OpenShift cluster."""
        reader = self._build_reader()

        # Core Kubernetes collection (workloads, quotas, NetworkPolicies, load).
        workloads, resources, policies, load, findings = collect_k8s_core(reader)

        # OpenShift Projects (analogous to namespaces, with display metadata).
        projects = reader.list_projects()
        resources["projects"] = len(projects)
        resources["project_names"] = [(p.get("metadata") or {}).get("name", "") for p in projects]

        # Routes (external exposure surface).
        routes = [summarise_route(r) for r in reader.list_routes()]
        resources["routes"] = routes

        # SCCs → policies + permissive-binding findings.
        sccs = [summarise_scc(s) for s in reader.list_scc()]
        policies = list(policies) + sccs
        findings = list(findings) + scc_findings(sccs)

        return InfraSnapshot(
            adapter=self.name,
            target=self.target,
            available=True,
            workloads=workloads,
            resources=resources,
            policies=policies,
            load=load,
            findings=findings,
        )


class _LiveOpenShiftReader:
    """Real :class:`OpenShiftReader` backed by the dynamic Kubernetes client.

    Constructed only inside :meth:`OpenShiftAdapter._build_reader`; SDKs are
    imported lazily here so the module imports cleanly without them. Core
    Kubernetes reads are delegated to the kubernetes-adapter's live reader.
    """

    def __init__(self, *, kubeconfig: str | None = None) -> None:
        from kubernetes import client  # lazy: real SDK only here
        from plan.enrich.adapters.kubernetes import _LiveK8sReader

        self._core = _LiveK8sReader(kubeconfig=kubeconfig)
        self._dynamic = client.CustomObjectsApi()

    # Delegate the Kubernetes-native reads.
    def list_namespaces(self) -> list[dict]:
        return self._core.list_namespaces()

    def list_deployments(self) -> list[dict]:
        return self._core.list_deployments()

    def list_statefulsets(self) -> list[dict]:
        return self._core.list_statefulsets()

    def list_daemonsets(self) -> list[dict]:
        return self._core.list_daemonsets()

    def list_hpas(self) -> list[dict]:
        return self._core.list_hpas()

    def list_network_policies(self) -> list[dict]:
        return self._core.list_network_policies()

    def list_resource_quotas(self) -> list[dict]:
        return self._core.list_resource_quotas()

    def pod_metrics(self) -> list[dict]:
        return self._core.pod_metrics()

    # OpenShift-specific reads via the dynamic API (read-only list calls).
    def list_projects(self) -> list[dict]:
        data = self._dynamic.list_cluster_custom_object("project.openshift.io", "v1", "projects")
        return list(data.get("items", []))

    def list_routes(self) -> list[dict]:
        data = self._dynamic.list_cluster_custom_object("route.openshift.io", "v1", "routes")
        return list(data.get("items", []))

    def list_scc(self) -> list[dict]:
        data = self._dynamic.list_cluster_custom_object(
            "security.openshift.io", "v1", "securitycontextconstraints"
        )
        return list(data.get("items", []))
