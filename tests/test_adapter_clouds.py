"""Tests for the read-only cloud inventory/posture adapters (#10).

Covers Azure, AWS, and GCP adapters built on the shared ``InfraAdapter`` base.
Fake readers supply canned data so no cloud SDK is required. A read-only guard
fake fails loudly on any mutating call (``create_*``/``delete_*``/``put_*``/
``update_*``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.enrich.adapters.aws import AwsAdapter  # noqa: E402
from plan.enrich.adapters.azure import AzureAdapter  # noqa: E402
from plan.enrich.adapters.gcp import GcpAdapter  # noqa: E402
from plan.enrich.base import (  # noqa: E402
    available_adapters,
    get_adapter,
)

# ── read-only guard ────────────────────────────────────────────────────


class ReadOnlyGuard:
    """Wraps a reader; raises if any mutating method is accessed.

    Mutating == any attribute whose name starts with create_/delete_/put_/
    update_. This proves the adapters only ever call read methods.
    """

    _BANNED = ("create_", "delete_", "put_", "update_")

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        if name.startswith(self._BANNED):
            raise AssertionError(f"read-only contract violated: {name}")
        return getattr(self._inner, name)


# ── fake readers ───────────────────────────────────────────────────────


class FakeAzureReader:
    def list_resource_groups(self) -> list[dict]:
        return [
            {"name": "rg-prod", "location": "westeurope"},
            {"name": "rg-dev", "location": "northeurope"},
        ]

    def list_aks_clusters(self) -> list[dict]:
        return [
            {
                "name": "aks-modern",
                "location": "westeurope",
                "kubernetes_version": "1.29.2",
                "node_count": 5,
            },
            {
                "name": "aks-legacy",
                "location": "northeurope",
                "kubernetes_version": "1.25.6",
                "node_count": 2,
                "public_exposed": True,
            },
        ]

    def list_policy_assignments(self) -> list[dict]:
        return [{"name": "require-tags", "display_name": "Require resource tags"}]


class FakeAwsReader:
    def list_ec2_instances(self) -> list[dict]:
        return [
            {
                "instance_id": "i-aaa",
                "instance_type": "t3.medium",
                "state": "running",
                "region": "eu-west-1",
            },
            {
                "instance_id": "i-bbb",
                "instance_type": "t3.medium",
                "state": "running",
                "region": "eu-west-1",
            },
        ]

    def list_eks_clusters(self) -> list[dict]:
        return [
            {"name": "eks-app", "version": "1.24", "status": "ACTIVE",
             "region": "eu-west-1"},
        ]

    def list_security_groups(self) -> list[dict]:
        return [
            {
                "group_id": "sg-open",
                "group_name": "world-ssh",
                "ip_permissions": [
                    {"ip_ranges": [{"cidr_ip": "0.0.0.0/0"}], "ipv6_ranges": []},
                ],
            },
            {
                "group_id": "sg-private",
                "group_name": "internal",
                "ip_permissions": [
                    {"ip_ranges": [{"cidr_ip": "10.0.0.0/8"}], "ipv6_ranges": []},
                ],
            },
        ]


class FakeGcpReader:
    def list_gke_clusters(self) -> list[dict]:
        return [
            {
                "name": "gke-prod",
                "location": "europe-west1",
                "current_master_version": "1.27.3-gke.100",
                "node_count": 4,
            },
        ]

    def list_instances(self) -> list[dict]:
        return [
            {"name": "vm-1", "machine_type": "e2-standard-4",
             "status": "RUNNING", "zone": "europe-west1-b"},
        ]

    def list_projects(self) -> list[dict]:
        return [{"project_id": "prod-123", "name": "Production"}]


class ExplodingReader:
    """Every read method raises — exercises graceful degradation."""

    def __getattr__(self, name: str) -> object:
        def _boom(*_a: object, **_k: object) -> object:
            raise RuntimeError("backend unreachable")

        return _boom


# ── registry ───────────────────────────────────────────────────────────


def test_cloud_adapters_registered():
    for name in ("azure", "aws", "gcp"):
        assert name in available_adapters()
    assert isinstance(get_adapter("azure", reader=FakeAzureReader()), AzureAdapter)


# ── Azure ──────────────────────────────────────────────────────────────


def test_azure_discover_populates_snapshot():
    adapter = AzureAdapter(target="sub-1", reader=ReadOnlyGuard(FakeAzureReader()))
    assert adapter.available() is True
    snap = adapter.discover()

    assert snap.adapter == "azure"
    assert snap.target == "sub-1"
    assert snap.available is True
    assert len(snap.workloads) == 2
    assert {w["kind"] for w in snap.workloads} == {"aks_cluster"}
    assert snap.resources["resource_group_count"] == 2
    assert snap.resources["aks_cluster_count"] == 2
    assert "westeurope" in snap.resources["regions"]
    assert snap.policies[0]["name"] == "require-tags"
    # outdated k8s + resource-group count + public exposure findings
    assert any("aks-legacy" in f and "outdated" in f for f in snap.findings)
    assert any("resource groups" in f for f in snap.findings)
    assert any("publicly exposed" in f for f in snap.findings)


def test_azure_available_env_logic(monkeypatch):
    for var in ("AZURE_SUBSCRIPTION_ID", "AZURE_CLIENT_ID", "AZURE_TENANT_ID",
                "AZURE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)

    adapter = AzureAdapter()
    assert adapter.available() is False

    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-x")
    assert adapter.available() is False  # subscription but no credential

    monkeypatch.setenv("AZURE_CLIENT_ID", "client-x")
    assert adapter.available() is True


# ── AWS ────────────────────────────────────────────────────────────────


def test_aws_discover_populates_snapshot():
    adapter = AwsAdapter(target="acct-1", reader=ReadOnlyGuard(FakeAwsReader()))
    assert adapter.available() is True
    snap = adapter.discover()

    assert snap.adapter == "aws"
    assert len(snap.workloads) == 1
    assert snap.workloads[0]["kind"] == "eks_cluster"
    assert snap.resources["ec2_instance_count"] == 2
    assert snap.resources["instance_types"]["t3.medium"] == 2
    assert snap.resources["security_group_count"] == 2
    # only the world-open SG becomes a policy entry
    assert len(snap.policies) == 1
    assert snap.policies[0]["group_id"] == "sg-open"
    assert any("outdated" in f and "eks-app" in f for f in snap.findings)
    assert any("open to the world" in f for f in snap.findings)


def test_aws_available_env_logic(monkeypatch):
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)

    adapter = AwsAdapter()
    assert adapter.available() is False

    monkeypatch.setenv("AWS_PROFILE", "default")
    assert adapter.available() is True


# ── GCP ────────────────────────────────────────────────────────────────


def test_gcp_discover_populates_snapshot():
    adapter = GcpAdapter(target="prod-123", reader=ReadOnlyGuard(FakeGcpReader()))
    assert adapter.available() is True
    snap = adapter.discover()

    assert snap.adapter == "gcp"
    assert snap.target == "prod-123"
    assert len(snap.workloads) == 1
    assert snap.workloads[0]["kind"] == "gke_cluster"
    assert snap.workloads[0]["kubernetes_version"].startswith("1.27")
    assert snap.resources["instance_count"] == 1
    assert snap.resources["project_count"] == 1
    assert snap.policies[0]["project_id"] == "prod-123"
    assert any("compute instances" in f for f in snap.findings)
    assert any("accessible projects" in f for f in snap.findings)


def test_gcp_available_env_logic(monkeypatch):
    for var in ("GOOGLE_CLOUD_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS",
                "GCLOUD_PROJECT", "CLOUDSDK_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    adapter = GcpAdapter()
    assert adapter.available() is False

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "prod-123")
    assert adapter.available() is False  # project but no credential

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    assert adapter.available() is True


# ── graceful degradation: to_enrichment never raises ───────────────────


@pytest.mark.parametrize(
    "adapter",
    [
        AzureAdapter(target="sub", reader=ExplodingReader()),
        AwsAdapter(target="acct", reader=ExplodingReader()),
        GcpAdapter(target="proj", reader=ExplodingReader()),
    ],
)
def test_to_enrichment_captures_error(adapter):
    enrichment = adapter.to_enrichment()  # must not raise
    assert enrichment["available"] is False
    assert enrichment["error"]
    assert "RuntimeError" in enrichment["error"]


def test_to_enrichment_happy_path_each_cloud():
    cases = [
        AzureAdapter(target="sub", reader=FakeAzureReader()),
        AwsAdapter(target="acct", reader=FakeAwsReader()),
        GcpAdapter(target="proj", reader=FakeGcpReader()),
    ]
    for adapter in cases:
        enrichment = adapter.to_enrichment()
        assert enrichment["available"] is True
        assert enrichment["error"] is None
        assert enrichment["resources"]
