"""Read-only Azure inventory + posture adapter (#10).

Discovers resource groups, AKS clusters, and an Azure Policy / posture
summary for a subscription. Strictly read-only: only ``list``/``get`` calls
are ever issued.

The adapter never imports the Azure SDK at module import time. Discovery runs
against an injected *reader* object exposing simple read methods::

    reader.list_resource_groups()  -> list[dict]
    reader.list_aks_clusters()     -> list[dict]
    reader.list_policy_assignments() -> list[dict]

In production the reader is built lazily from ``azure-identity`` +
``azure-mgmt-*`` (see :func:`_build_reader`). Tests inject a fake reader and
need no Azure SDK installed.
"""

from __future__ import annotations

import os
from typing import Protocol

from ..base import InfraAdapter, InfraSnapshot, register_adapter
from ._shared import parse_k8s_version

# Kubernetes versions at/below this are flagged as outdated in findings.
_OUTDATED_K8S_BELOW = (1, 28)


class AzureReader(Protocol):
    """Read-only seam consumed by :class:`AzureAdapter`."""

    def list_resource_groups(self) -> list[dict]: ...
    def list_aks_clusters(self) -> list[dict]: ...
    def list_policy_assignments(self) -> list[dict]: ...


def _build_reader(subscription_id: str) -> AzureReader:  # pragma: no cover
    """Lazily construct a real Azure reader from the SDK.

    Imported here (not at module scope) so the adapter stays importable with no
    Azure packages installed. Only list operations are used.
    """
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.containerservice import ContainerServiceClient
    from azure.mgmt.resource import ResourceManagementClient
    from azure.mgmt.resource.policy import PolicyClient

    credential = DefaultAzureCredential()

    class _SdkReader:
        def list_resource_groups(self) -> list[dict]:
            client = ResourceManagementClient(credential, subscription_id)
            return [
                {"name": rg.name, "location": rg.location} for rg in client.resource_groups.list()
            ]

        def list_aks_clusters(self) -> list[dict]:
            client = ContainerServiceClient(credential, subscription_id)
            out: list[dict] = []
            for c in client.managed_clusters.list():
                node_count = sum(getattr(p, "count", 0) or 0 for p in (c.agent_pool_profiles or []))
                out.append(
                    {
                        "name": c.name,
                        "location": c.location,
                        "kubernetes_version": c.kubernetes_version,
                        "node_count": node_count,
                    }
                )
            return out

        def list_policy_assignments(self) -> list[dict]:
            client = PolicyClient(credential, subscription_id)
            return [
                {"name": a.name, "display_name": a.display_name}
                for a in client.policy_assignments.list()
            ]

    return _SdkReader()


@register_adapter
class AzureAdapter(InfraAdapter):
    """Read-only Azure subscription inventory + posture adapter."""

    name = "azure"

    def __init__(
        self,
        *,
        target: str = "",
        reader: AzureReader | None = None,
        **options: object,
    ) -> None:
        super().__init__(target=target, **options)
        self._reader = reader

    # ── availability ────────────────────────────────────────────────────
    def available(self) -> bool:
        """True if a reader is injected or subscription credentials exist."""
        if self._reader is not None:
            return True
        subscription = self.target or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
        if not subscription:
            return False
        has_sp_creds = bool(os.environ.get("AZURE_CLIENT_ID") or os.environ.get("AZURE_TENANT_ID"))
        has_cli = bool(os.environ.get("AZURE_CONFIG_DIR"))
        return has_sp_creds or has_cli

    def _reader_or_build(self) -> AzureReader:
        if self._reader is not None:
            return self._reader
        subscription = self.target or os.environ.get("AZURE_SUBSCRIPTION_ID", "")
        return _build_reader(subscription)

    # ── discovery ───────────────────────────────────────────────────────
    def discover(self) -> InfraSnapshot:
        """Return a read-only snapshot of the Azure subscription."""
        reader = self._reader_or_build()
        subscription = self.target or os.environ.get("AZURE_SUBSCRIPTION_ID", "")

        resource_groups = list(reader.list_resource_groups())
        clusters = list(reader.list_aks_clusters())
        policies_raw = list(reader.list_policy_assignments())

        workloads: list[dict] = []
        findings: list[str] = []
        regions: set[str] = set()
        public_exposed = 0

        for c in clusters:
            name = c.get("name", "unknown")
            version = c.get("kubernetes_version") or c.get("version")
            region = c.get("location") or c.get("region")
            if region:
                regions.add(region)
            if c.get("public") or c.get("public_exposed"):
                public_exposed += 1
            workloads.append(
                {
                    "kind": "aks_cluster",
                    "name": name,
                    "node_count": c.get("node_count", 0),
                    "kubernetes_version": version,
                    "region": region,
                }
            )
            parsed = parse_k8s_version(version)
            if parsed is not None and parsed < _OUTDATED_K8S_BELOW:
                findings.append(f"AKS cluster {name} runs an outdated k8s version {version}")

        for rg in resource_groups:
            loc = rg.get("location") or rg.get("region")
            if loc:
                regions.add(loc)

        if resource_groups:
            findings.append(f"{len(resource_groups)} resource groups")
        if public_exposed:
            findings.append(f"{public_exposed} AKS clusters are publicly exposed")

        resources = {
            "resource_group_count": len(resource_groups),
            "aks_cluster_count": len(clusters),
            "regions": sorted(regions),
        }
        policies = [
            {
                "name": p.get("name"),
                "display_name": p.get("display_name") or p.get("displayName"),
            }
            for p in policies_raw
        ]

        return InfraSnapshot(
            adapter=self.name,
            target=subscription,
            available=True,
            workloads=workloads,
            resources=resources,
            policies=policies,
            findings=findings,
            raw={
                "resource_groups": resource_groups,
                "aks_clusters": clusters,
                "policy_assignments": policies_raw,
            },
        )


__all__: list[str] = ["AzureAdapter", "AzureReader"]
