"""Tests for the read-only Kubernetes infra adapter (#8).

A fake :class:`K8sReader` returns canned cluster data so the tests need no real
``kubernetes`` SDK. The fake also enforces the read-only contract: any attempt
to access a mutating method (``create_*``/``delete_*``/``patch_*``/``replace_*``)
raises, so the test proves the adapter never tries to write.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

pytest.importorskip("pydantic")

from plan.enrich.adapters.kubernetes import KubernetesAdapter  # noqa: E402
from plan.enrich.base import InfraSnapshot, get_adapter  # noqa: E402

_WRITE_PREFIXES = ("create_", "delete_", "patch_", "replace_", "apply_", "scale_")


class FakeK8sReader:
    """Canned read-only cluster. Raises on any mutating attribute access."""

    def __init__(self, *, with_metrics: bool = True) -> None:
        self._with_metrics = with_metrics

    def __getattr__(self, name: str) -> object:
        if name.startswith(_WRITE_PREFIXES):
            raise AssertionError(f"read-only contract violated: adapter called {name}()")
        raise AttributeError(name)

    def list_namespaces(self) -> list[dict]:
        return [
            {"metadata": {"name": "default"}},
            {"metadata": {"name": "payments"}},
        ]

    def list_deployments(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "api", "namespace": "payments"},
                "spec": {
                    "replicas": 3,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "api",
                                    "resources": {
                                        "requests": {"cpu": "250m", "memory": "256Mi"},
                                        "limits": {"cpu": "500m", "memory": "512Mi"},
                                    },
                                }
                            ]
                        }
                    },
                },
            },
            {
                "metadata": {"name": "worker", "namespace": "default"},
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "worker",
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "128Mi"}
                                        # no limits → should be flagged
                                    },
                                }
                            ]
                        }
                    },
                },
            },
        ]

    def list_statefulsets(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "db", "namespace": "payments"},
                "spec": {
                    "replicas": 2,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "postgres",
                                    "resources": {
                                        "requests": {"cpu": "1", "memory": "2Gi"},
                                        "limits": {"cpu": "2", "memory": "4Gi"},
                                    },
                                }
                            ]
                        }
                    },
                },
            }
        ]

    def list_daemonsets(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "node-agent", "namespace": "default"},
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "agent",
                                    "resources": {
                                        "limits": {"cpu": "100m", "memory": "64Mi"},
                                        "requests": {"cpu": "100m", "memory": "64Mi"},
                                    },
                                }
                            ]
                        }
                    }
                },
            }
        ]

    def list_hpas(self) -> list[dict]:
        return []

    def list_network_policies(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "default-deny", "namespace": "payments"},
                "spec": {"policyTypes": ["Ingress", "Egress"]},
            }
        ]

    def list_resource_quotas(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "compute", "namespace": "payments"},
                "spec": {"hard": {"cpu": "4", "memory": "8Gi"}},
                "status": {"used": {"cpu": "2", "memory": "4Gi"}},
            }
        ]

    def pod_metrics(self) -> list[dict]:
        if not self._with_metrics:
            return []
        return [
            {
                "metadata": {"name": "api-1", "namespace": "payments"},
                "containers": [{"usage": {"cpu": "120m", "memory": "200Mi"}}],
            }
        ]


def test_discover_populates_snapshot():
    adapter = KubernetesAdapter(target="prod", reader=FakeK8sReader())
    snapshot = adapter.discover()

    assert isinstance(snapshot, InfraSnapshot)
    assert snapshot.adapter == "kubernetes"
    assert snapshot.target == "prod"
    assert snapshot.available is True

    # Workloads from all three kinds present.
    kinds = {w["kind"] for w in snapshot.workloads}
    assert kinds == {"Deployment", "StatefulSet", "DaemonSet"}
    api = next(w for w in snapshot.workloads if w["name"] == "api")
    assert api["replicas"] == 3
    assert api["containers"][0]["limits"]["cpu"] == "500m"


def test_resources_and_quotas():
    snapshot = KubernetesAdapter(reader=FakeK8sReader()).discover()
    resources = snapshot.resources
    assert resources["namespaces"] == 2
    assert "payments" in resources["namespace_names"]
    assert resources["totals"]["requests"]["cpu_millicores"] > 0
    assert resources["totals"]["limits"]["memory_bytes"] > 0
    assert len(resources["resource_quotas"]) == 1
    assert resources["resource_quotas"][0]["hard"]["cpu"] == "4"


def test_policies_and_findings():
    snapshot = KubernetesAdapter(reader=FakeK8sReader()).discover()

    policy_names = {p["name"] for p in snapshot.policies}
    assert "default-deny" in policy_names

    findings = "\n".join(snapshot.findings)
    # default namespace has no NetworkPolicy.
    assert "namespace default has no NetworkPolicy" in findings
    # worker deployment has no resource limits.
    assert "default/worker has no resource limits" in findings


def test_load_from_pod_metrics():
    with_metrics = KubernetesAdapter(reader=FakeK8sReader(with_metrics=True)).discover()
    assert with_metrics.load["pods_measured"] == 1
    assert with_metrics.load["cpu_millicores"] > 0

    without = KubernetesAdapter(reader=FakeK8sReader(with_metrics=False)).discover()
    assert without.load == {}


def test_available_logic(tmp_path, monkeypatch):
    # Injected reader → always available.
    assert KubernetesAdapter(reader=FakeK8sReader()).available() is True

    # No reader and no kubeconfig anywhere → not available.
    monkeypatch.delenv("KUBECONFIG", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert KubernetesAdapter().available() is False

    # An explicit kubeconfig file → available.
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n")
    assert KubernetesAdapter(kubeconfig=str(kubeconfig)).available() is True


def test_to_enrichment_returns_dict_and_never_raises():
    enrichment = KubernetesAdapter(reader=FakeK8sReader()).to_enrichment()
    assert isinstance(enrichment, dict)
    assert enrichment["adapter"] == "kubernetes"
    assert enrichment["available"] is True
    assert enrichment["workloads"]

    # A reader that explodes on read must degrade gracefully, not raise.
    class Boom:
        def list_namespaces(self) -> list[dict]:
            raise RuntimeError("boom")

    result = KubernetesAdapter(reader=Boom()).to_enrichment()
    assert isinstance(result, dict)
    assert result["available"] is False
    assert "boom" in (result["error"] or "")


def test_registered_via_get_adapter():
    adapter = get_adapter("kubernetes", reader=FakeK8sReader())
    assert isinstance(adapter, KubernetesAdapter)
    assert adapter.discover().workloads
