"""Read-only GCP inventory + posture adapter (#10).

Discovers GKE clusters, Compute Engine instances, and accessible projects.
Strictly read-only: only ``list``/``get`` calls are ever issued.

The adapter never imports the Google SDKs at module import time. Discovery runs
against an injected *reader* object exposing simple read methods::

    reader.list_gke_clusters()  -> list[dict]
    reader.list_instances()     -> list[dict]
    reader.list_projects()      -> list[dict]

In production the reader is built lazily from ``google-cloud-*`` (see
:func:`_build_reader`). Tests inject a fake reader and need no Google SDK
installed.
"""

from __future__ import annotations

import os
from typing import Protocol

from ..base import InfraAdapter, InfraSnapshot, register_adapter

# Kubernetes versions at/below this are flagged as outdated in findings.
_OUTDATED_K8S_BELOW = (1, 28)


class GcpReader(Protocol):
    """Read-only seam consumed by :class:`GcpAdapter`."""

    def list_gke_clusters(self) -> list[dict]: ...
    def list_instances(self) -> list[dict]: ...
    def list_projects(self) -> list[dict]: ...


def _parse_k8s_version(version: str | None) -> tuple[int, int] | None:
    """Parse ``"1.27.3-gke.100"`` -> ``(1, 27)``; ``None`` if unparsable."""
    if not version:
        return None
    parts = str(version).lstrip("v").split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return None


def _build_reader(project: str) -> GcpReader:  # pragma: no cover
    """Lazily construct a real GCP reader from the SDK (read-only calls only)."""
    from google.cloud import compute_v1, container_v1, resourcemanager_v3

    class _SdkReader:
        def list_gke_clusters(self) -> list[dict]:
            client = container_v1.ClusterManagerClient()
            parent = f"projects/{project}/locations/-"
            resp = client.list_clusters(parent=parent)
            return [
                {
                    "name": c.name,
                    "location": c.location,
                    "current_master_version": c.current_master_version,
                    "node_count": c.current_node_count,
                }
                for c in resp.clusters
            ]

        def list_instances(self) -> list[dict]:
            client = compute_v1.InstancesClient()
            out: list[dict] = []
            for zone, scoped in client.aggregated_list(project=project):
                for inst in getattr(scoped, "instances", []) or []:
                    out.append({
                        "name": inst.name,
                        "machine_type": inst.machine_type.split("/")[-1],
                        "status": inst.status,
                        "zone": zone.split("/")[-1],
                    })
            return out

        def list_projects(self) -> list[dict]:
            client = resourcemanager_v3.ProjectsClient()
            return [
                {"project_id": p.project_id, "name": p.display_name}
                for p in client.search_projects()
            ]

    return _SdkReader()


@register_adapter
class GcpAdapter(InfraAdapter):
    """Read-only GCP project inventory + posture adapter."""

    name = "gcp"

    def __init__(
        self,
        *,
        target: str = "",
        reader: GcpReader | None = None,
        **options: object,
    ) -> None:
        super().__init__(target=target, **options)
        self._reader = reader

    # ── availability ────────────────────────────────────────────────────
    def available(self) -> bool:
        """True if a reader is injected or GCP credentials/project exist."""
        if self._reader is not None:
            return True
        project = self.target or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        if not project:
            return False
        has_creds = bool(
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            or os.environ.get("GCLOUD_PROJECT")
            or os.environ.get("CLOUDSDK_CONFIG")
        )
        return has_creds

    def _project(self) -> str:
        return self.target or os.environ.get("GOOGLE_CLOUD_PROJECT", "")

    def _reader_or_build(self) -> GcpReader:
        if self._reader is not None:
            return self._reader
        return _build_reader(self._project())

    # ── discovery ───────────────────────────────────────────────────────
    def discover(self) -> InfraSnapshot:
        """Return a read-only snapshot of the GCP project."""
        reader = self._reader_or_build()
        project = self._project()

        clusters = list(reader.list_gke_clusters())
        instances = list(reader.list_instances())
        projects = list(reader.list_projects())

        workloads: list[dict] = []
        findings: list[str] = []
        regions: set[str] = set()
        machine_types: dict[str, int] = {}

        for c in clusters:
            name = c.get("name", "unknown")
            version = c.get("current_master_version") or c.get("version")
            location = c.get("location") or c.get("region")
            if location:
                regions.add(location)
            workloads.append({
                "kind": "gke_cluster",
                "name": name,
                "node_count": c.get("node_count", 0),
                "kubernetes_version": version,
                "location": location,
            })
            parsed = _parse_k8s_version(version)
            if parsed is not None and parsed < _OUTDATED_K8S_BELOW:
                findings.append(
                    f"GKE cluster {name} runs an outdated k8s version {version}"
                )

        for inst in instances:
            zone = inst.get("zone")
            if zone:
                regions.add(zone)
            mtype = inst.get("machine_type")
            if mtype:
                machine_types[mtype] = machine_types.get(mtype, 0) + 1

        if instances:
            findings.append(f"{len(instances)} compute instances")
        if projects:
            findings.append(f"{len(projects)} accessible projects")

        resources = {
            "gke_cluster_count": len(clusters),
            "instance_count": len(instances),
            "project_count": len(projects),
            "machine_types": machine_types,
            "regions": sorted(regions),
        }
        policies = [
            {"project_id": p.get("project_id"), "name": p.get("name")}
            for p in projects
        ]

        return InfraSnapshot(
            adapter=self.name,
            target=project,
            available=True,
            workloads=workloads,
            resources=resources,
            policies=policies,
            findings=findings,
            raw={
                "gke_clusters": clusters,
                "instances": instances,
                "projects": projects,
            },
        )


__all__: list[str] = ["GcpAdapter", "GcpReader"]
