"""Read-only Kubernetes infrastructure adapter (#8).

Introspects a live Kubernetes cluster via list/get calls only and assembles
an :class:`~plan.enrich.base.InfraSnapshot`: workloads (deployments,
statefulsets, daemonsets with container resource requests/limits), cluster
resources (namespace count, aggregate requests/limits, resource quotas),
policies (NetworkPolicies), live load (from the metrics API if reachable) and
human-readable findings (missing limits, namespaces without NetworkPolicies).

**Read-only is a hard contract.** This adapter only ever issues
``list_*``/``read_*`` reads through the injected :class:`K8sReader`. It never
creates, updates, patches, scales or deletes anything.

Design for testability: the real ``kubernetes`` python client is *lazily*
imported inside :meth:`KubernetesAdapter._build_reader` and never at module
import time. Tests inject a fake reader implementing :class:`K8sReader` and
need no real SDK installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from plan.enrich.base import InfraAdapter, InfraSnapshot, register_adapter


@runtime_checkable
class K8sReader(Protocol):
    """Read-only seam over a Kubernetes cluster.

    Every method performs a single list/get read and returns plain JSON-able
    structures (lists of dicts). Implementations MUST NOT expose or perform any
    mutating operation. :meth:`pod_metrics` is optional — return an empty list
    if the metrics API is unavailable.
    """

    def list_namespaces(self) -> list[dict]: ...

    def list_deployments(self) -> list[dict]: ...

    def list_statefulsets(self) -> list[dict]: ...

    def list_daemonsets(self) -> list[dict]: ...

    def list_hpas(self) -> list[dict]: ...

    def list_network_policies(self) -> list[dict]: ...

    def list_resource_quotas(self) -> list[dict]: ...

    def pod_metrics(self) -> list[dict]: ...


# ── helpers (shared with the OpenShift adapter) ─────────────────────────


def _parse_cpu(value: str | None) -> float:
    """Parse a Kubernetes CPU quantity into millicores. Unknown → 0.0."""
    if not value:
        return 0.0
    text = str(value).strip()
    try:
        if text.endswith("m"):
            return float(text[:-1])
        if text.endswith("n"):
            return float(text[:-1]) / 1_000_000.0
        if text.endswith("u"):
            return float(text[:-1]) / 1_000.0
        return float(text) * 1000.0
    except ValueError:
        return 0.0


_MEM_UNITS = {
    "Ki": 1024.0,
    "Mi": 1024.0**2,
    "Gi": 1024.0**3,
    "Ti": 1024.0**4,
    "K": 1000.0,
    "M": 1000.0**2,
    "G": 1000.0**3,
    "T": 1000.0**4,
}


def _parse_mem(value: str | None) -> float:
    """Parse a Kubernetes memory quantity into bytes. Unknown → 0.0."""
    if not value:
        return 0.0
    text = str(value).strip()
    for suffix, factor in _MEM_UNITS.items():
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * factor
            except ValueError:
                return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _container_resources(container: dict) -> dict:
    """Extract cpu/memory requests & limits for one container spec."""
    resources = container.get("resources") or {}
    requests = resources.get("requests") or {}
    limits = resources.get("limits") or {}
    return {
        "name": container.get("name", ""),
        "requests": {
            "cpu": requests.get("cpu"),
            "memory": requests.get("memory"),
        },
        "limits": {
            "cpu": limits.get("cpu"),
            "memory": limits.get("memory"),
        },
    }


def summarise_workload(item: dict, kind: str) -> dict:
    """Normalise a workload object (deployment/statefulset/daemonset)."""
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    pod_spec = ((spec.get("template") or {}).get("spec")) or {}
    containers = [_container_resources(c) for c in (pod_spec.get("containers") or [])]
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "kind": kind,
        "replicas": spec.get("replicas"),
        "containers": containers,
    }


def workload_has_limits(workload: dict) -> bool:
    """True only if every container declares both cpu and memory limits."""
    containers = workload.get("containers") or []
    if not containers:
        return False
    for container in containers:
        limits = container.get("limits") or {}
        if not limits.get("cpu") or not limits.get("memory"):
            return False
    return True


def aggregate_resources(workloads: list[dict]) -> dict:
    """Sum container requests/limits across workloads (cpu millicores, mem bytes)."""
    totals = {
        "requests": {"cpu_millicores": 0.0, "memory_bytes": 0.0},
        "limits": {"cpu_millicores": 0.0, "memory_bytes": 0.0},
    }
    for workload in workloads:
        for container in workload.get("containers") or []:
            requests = container.get("requests") or {}
            limits = container.get("limits") or {}
            totals["requests"]["cpu_millicores"] += _parse_cpu(requests.get("cpu"))
            totals["requests"]["memory_bytes"] += _parse_mem(requests.get("memory"))
            totals["limits"]["cpu_millicores"] += _parse_cpu(limits.get("cpu"))
            totals["limits"]["memory_bytes"] += _parse_mem(limits.get("memory"))
    return totals


def summarise_network_policy(item: dict) -> dict:
    """Normalise a NetworkPolicy object."""
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "kind": "NetworkPolicy",
        "policy_types": spec.get("policyTypes") or [],
    }


def summarise_resource_quota(item: dict) -> dict:
    """Normalise a ResourceQuota object."""
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    return {
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "hard": spec.get("hard") or {},
        "used": status.get("used") or {},
    }


def aggregate_pod_metrics(metrics: list[dict]) -> dict:
    """Sum pod-level cpu/memory usage from the metrics API."""
    total_cpu = 0.0
    total_mem = 0.0
    for pod in metrics or []:
        for container in pod.get("containers") or []:
            usage = container.get("usage") or {}
            total_cpu += _parse_cpu(usage.get("cpu"))
            total_mem += _parse_mem(usage.get("memory"))
    return {
        "pods_measured": len(metrics or []),
        "cpu_millicores": total_cpu,
        "memory_bytes": total_mem,
    }


def collect_k8s_core(reader: K8sReader) -> tuple[list[dict], dict, list[dict], dict, list[str]]:
    """Run the core Kubernetes read flow shared with OpenShift.

    Returns ``(workloads, resources, policies, load, findings)``.
    """
    namespaces = reader.list_namespaces()
    namespace_names = [(ns.get("metadata") or {}).get("name", "") for ns in namespaces]

    workloads: list[dict] = []
    workloads += [summarise_workload(d, "Deployment") for d in reader.list_deployments()]
    workloads += [summarise_workload(s, "StatefulSet") for s in reader.list_statefulsets()]
    workloads += [summarise_workload(d, "DaemonSet") for d in reader.list_daemonsets()]

    quotas = [summarise_resource_quota(q) for q in reader.list_resource_quotas()]
    resources = {
        "namespaces": len(namespaces),
        "namespace_names": namespace_names,
        "totals": aggregate_resources(workloads),
        "resource_quotas": quotas,
    }

    network_policies = [summarise_network_policy(p) for p in reader.list_network_policies()]
    policies: list[dict] = list(network_policies)

    findings: list[str] = []

    # Findings: workloads missing resource limits.
    for workload in workloads:
        if not workload_has_limits(workload):
            findings.append(
                f"{workload['kind'].lower()} "
                f"{workload['namespace']}/{workload['name']} has no resource limits"
            )

    # Findings: namespaces with no NetworkPolicy.
    namespaces_with_policy = {p["namespace"] for p in network_policies}
    for name in namespace_names:
        if name and name not in namespaces_with_policy:
            findings.append(f"namespace {name} has no NetworkPolicy")

    load: dict = {}
    pod_metrics = list(getattr(reader, "pod_metrics", lambda: [])() or [])
    if pod_metrics:
        load = aggregate_pod_metrics(pod_metrics)

    return workloads, resources, policies, load, findings


def _kubeconfig_present(kubeconfig: str | None) -> bool:
    """True if any kubeconfig source or in-cluster token is reachable."""
    if kubeconfig and Path(kubeconfig).expanduser().is_file():
        return True
    env_kubeconfig = os.environ.get("KUBECONFIG")
    if env_kubeconfig:
        for part in env_kubeconfig.split(os.pathsep):
            if part and Path(part).expanduser().is_file():
                return True
    if (Path.home() / ".kube" / "config").is_file():
        return True
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    return token.is_file()


@register_adapter
class KubernetesAdapter(InfraAdapter):
    """Read-only adapter for a live Kubernetes cluster (#8)."""

    name = "kubernetes"

    def __init__(
        self,
        *,
        target: str = "",
        reader: K8sReader | None = None,
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
        return _kubeconfig_present(self.kubeconfig)

    def _build_reader(self) -> K8sReader:
        """Return the injected reader, or build a real one lazily.

        The real ``kubernetes`` client is imported here, never at module load,
        so tests that inject a fake reader need no SDK installed.
        """
        if self._reader is not None:
            return self._reader
        return _LiveK8sReader(kubeconfig=self.kubeconfig)

    def discover(self) -> InfraSnapshot:
        """Assemble a read-only snapshot of the cluster."""
        reader = self._build_reader()
        workloads, resources, policies, load, findings = collect_k8s_core(reader)
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


class _LiveK8sReader:
    """Real :class:`K8sReader` backed by the ``kubernetes`` python client.

    Constructed only inside :meth:`KubernetesAdapter._build_reader`; the SDK is
    imported lazily here so the module imports cleanly without it.
    """

    def __init__(self, *, kubeconfig: str | None = None) -> None:
        from kubernetes import client, config  # lazy: real SDK only here

        try:
            config.load_kube_config(config_file=kubeconfig)
        except Exception:
            config.load_incluster_config()
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        self._autoscaling = client.AutoscalingV1Api()
        self._networking = client.NetworkingV1Api()

    @staticmethod
    def _to_dicts(listing: object) -> list[dict]:
        return [item.to_dict() for item in getattr(listing, "items", [])]

    def list_namespaces(self) -> list[dict]:
        return self._to_dicts(self._core.list_namespace())

    def list_deployments(self) -> list[dict]:
        return self._to_dicts(self._apps.list_deployment_for_all_namespaces())

    def list_statefulsets(self) -> list[dict]:
        return self._to_dicts(self._apps.list_stateful_set_for_all_namespaces())

    def list_daemonsets(self) -> list[dict]:
        return self._to_dicts(self._apps.list_daemon_set_for_all_namespaces())

    def list_hpas(self) -> list[dict]:
        return self._to_dicts(self._autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces())

    def list_network_policies(self) -> list[dict]:
        return self._to_dicts(self._networking.list_network_policy_for_all_namespaces())

    def list_resource_quotas(self) -> list[dict]:
        return self._to_dicts(self._core.list_resource_quota_for_all_namespaces())

    def pod_metrics(self) -> list[dict]:
        from kubernetes import client  # lazy: real SDK only here

        custom = client.CustomObjectsApi()
        try:
            data = custom.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
        except Exception:
            return []
        return list(data.get("items", []))
