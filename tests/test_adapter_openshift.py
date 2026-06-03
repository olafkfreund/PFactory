"""Tests for the read-only OpenShift infra adapter (#9).

A fake :class:`OpenShiftReader` returns canned cluster data (Kubernetes core
plus Projects/Routes/SCCs) so the tests need no real ``kubernetes``/``openshift``
SDK. The fake raises on any mutating method, proving the adapter never writes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

pytest.importorskip("pydantic")

from plan.enrich.adapters.openshift import OpenShiftAdapter  # noqa: E402
from plan.enrich.base import InfraSnapshot, get_adapter  # noqa: E402

_WRITE_PREFIXES = ("create_", "delete_", "patch_", "replace_", "apply_", "scale_")


class FakeOpenShiftReader:
    """Canned read-only OpenShift cluster. Raises on any mutating access."""

    def __getattr__(self, name: str) -> object:
        if name.startswith(_WRITE_PREFIXES):
            raise AssertionError(f"read-only contract violated: adapter called {name}()")
        raise AttributeError(name)

    # ── Kubernetes core ──
    def list_namespaces(self) -> list[dict]:
        return [{"metadata": {"name": "app"}}]

    def list_deployments(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "frontend", "namespace": "app"},
                "spec": {
                    "replicas": 2,
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "web",
                                    "resources": {
                                        "requests": {"cpu": "200m", "memory": "256Mi"},
                                        "limits": {"cpu": "400m", "memory": "512Mi"},
                                    },
                                }
                            ]
                        }
                    },
                },
            }
        ]

    def list_statefulsets(self) -> list[dict]:
        return []

    def list_daemonsets(self) -> list[dict]:
        return []

    def list_hpas(self) -> list[dict]:
        return []

    def list_network_policies(self) -> list[dict]:
        return []

    def list_resource_quotas(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "quota", "namespace": "app"},
                "spec": {"hard": {"pods": "10"}},
                "status": {"used": {"pods": "3"}},
            }
        ]

    def pod_metrics(self) -> list[dict]:
        return []

    # ── OpenShift-specific ──
    def list_projects(self) -> list[dict]:
        return [
            {"metadata": {"name": "app"}},
            {"metadata": {"name": "infra"}},
        ]

    def list_routes(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "frontend", "namespace": "app"},
                "spec": {
                    "host": "app.example.com",
                    "to": {"name": "frontend"},
                    "tls": {"termination": "edge"},
                },
            }
        ]

    def list_scc(self) -> list[dict]:
        return [
            {
                "metadata": {"name": "restricted"},
                "allowPrivilegedContainer": False,
                "runAsUser": {"type": "MustRunAsRange"},
                "users": [],
                "groups": ["system:authenticated"],
            },
            {
                "metadata": {"name": "anyuid"},
                "allowPrivilegedContainer": False,
                "runAsUser": {"type": "RunAsAny"},
                "users": ["system:serviceaccount:app:builder"],
                "groups": [],
            },
            {
                "metadata": {"name": "privileged"},
                "allowPrivilegedContainer": True,
                "runAsUser": {"type": "RunAsAny"},
                "users": [],
                "groups": [],  # not bound → must NOT be flagged
            },
        ]


def test_discover_populates_core_and_openshift_resources():
    adapter = OpenShiftAdapter(target="ocp", reader=FakeOpenShiftReader())
    snapshot = adapter.discover()

    assert isinstance(snapshot, InfraSnapshot)
    assert snapshot.adapter == "openshift"
    assert snapshot.target == "ocp"
    assert snapshot.available is True

    # Kubernetes core workloads still collected.
    assert any(w["name"] == "frontend" for w in snapshot.workloads)

    # OpenShift-specific resources.
    assert snapshot.resources["projects"] == 2
    assert "infra" in snapshot.resources["project_names"]
    routes = snapshot.resources["routes"]
    assert routes[0]["host"] == "app.example.com"
    assert routes[0]["tls"] is True


def test_policies_include_scc_summaries():
    snapshot = OpenShiftAdapter(reader=FakeOpenShiftReader()).discover()
    scc_policies = [p for p in snapshot.policies if p.get("kind") == "SecurityContextConstraints"]
    names = {p["name"] for p in scc_policies}
    assert names == {"restricted", "anyuid", "privileged"}
    privileged = next(p for p in scc_policies if p["name"] == "privileged")
    assert privileged["allow_privileged_container"] is True


def test_findings_flag_bound_permissive_scc_only():
    snapshot = OpenShiftAdapter(reader=FakeOpenShiftReader()).discover()
    findings = "\n".join(snapshot.findings)

    # anyuid is bound to a serviceaccount → flagged.
    assert "permissive SCC 'anyuid'" in findings
    assert "system:serviceaccount:app:builder" in findings

    # privileged SCC has no bindings → must NOT be flagged.
    assert "permissive SCC 'privileged'" not in findings


def test_available_logic(tmp_path, monkeypatch):
    assert OpenShiftAdapter(reader=FakeOpenShiftReader()).available() is True

    monkeypatch.delenv("KUBECONFIG", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert OpenShiftAdapter().available() is False

    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n")
    assert OpenShiftAdapter(kubeconfig=str(kubeconfig)).available() is True


def test_to_enrichment_returns_dict_and_never_raises():
    enrichment = OpenShiftAdapter(reader=FakeOpenShiftReader()).to_enrichment()
    assert isinstance(enrichment, dict)
    assert enrichment["adapter"] == "openshift"
    assert enrichment["available"] is True

    class Boom:
        def list_namespaces(self) -> list[dict]:
            raise RuntimeError("boom")

    result = OpenShiftAdapter(reader=Boom()).to_enrichment()
    assert isinstance(result, dict)
    assert result["available"] is False
    assert "boom" in (result["error"] or "")


def test_registered_via_get_adapter():
    adapter = get_adapter("openshift", reader=FakeOpenShiftReader())
    assert isinstance(adapter, OpenShiftAdapter)
    assert adapter.discover().resources["projects"] == 2
