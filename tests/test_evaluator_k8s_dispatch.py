"""Tests for the Evaluator → Kubernetes port-forward wiring (#108).

Covers `_resolve_target` (which target a subtask uses) and `_kube_runtime_for`
(build a KubernetesRuntime for a port-forward target, else None). The runtime
class is injected — no real kubectl, no cluster.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.evaluator import _kube_runtime_for, _resolve_target

_HTTP = {"name": "api", "type": "http", "base_url": "https://api.example.com/"}
_K8S = {
    "name": "cluster",
    "type": "kubernetes",
    "context": "prod-readonly",
    "namespace": "example-app",
    "service": "web",
    "port": 8080,
    "port_forward": True,
}


def _snapshot(spec_dir: Path, targets: list[dict], default: str | None = None) -> None:
    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"targets": targets}
    if default:
        cfg["default_target"] = default
    (ctx / "pfactory_yml.json").write_text(json.dumps(cfg))


# ── _resolve_target ──────────────────────────────────────────────────────────


def test_resolve_target_by_name(tmp_path) -> None:
    _snapshot(tmp_path, [_HTTP, _K8S])
    t = _resolve_target(tmp_path, {"target_name": "cluster"})
    assert t["type"] == "kubernetes" and t["name"] == "cluster"


def test_resolve_target_falls_back_to_default_then_first(tmp_path) -> None:
    _snapshot(tmp_path, [_HTTP, _K8S], default="cluster")
    assert _resolve_target(tmp_path, {})["name"] == "cluster"  # default_target
    _snapshot(tmp_path, [_HTTP, _K8S])
    assert _resolve_target(tmp_path, {})["name"] == "api"  # first target


def test_resolve_target_none_without_snapshot(tmp_path) -> None:
    assert _resolve_target(tmp_path, {"target_name": "x"}) is None


# ── _kube_runtime_for ────────────────────────────────────────────────────────


class _FakeRuntime:
    instances: list = []

    def __init__(self, target, *, kubeconfig=None):
        self.target = target
        self.kubeconfig = kubeconfig
        self.target_url = "http://localhost:8080"
        _FakeRuntime.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_kube_runtime_none_for_http_target() -> None:
    assert _kube_runtime_for(_HTTP, runtime_cls=_FakeRuntime) is None


def test_kube_runtime_none_when_port_forward_disabled() -> None:
    t = dict(_K8S, port_forward=False)
    assert _kube_runtime_for(t, runtime_cls=_FakeRuntime) is None


def test_kube_runtime_none_for_missing_target() -> None:
    assert _kube_runtime_for(None, runtime_cls=_FakeRuntime) is None


def test_kube_runtime_built_for_port_forward_target(monkeypatch) -> None:
    _FakeRuntime.instances.clear()
    monkeypatch.setenv("KUBECONFIG", "/tmp/kube/config")
    rt = _kube_runtime_for(_K8S, runtime_cls=_FakeRuntime)
    assert rt is not None
    # target carries the attrs KubernetesRuntime.port_forward_argv reads
    assert rt.target.service == "web" and rt.target.port == 8080
    assert rt.target.context == "prod-readonly" and rt.target.namespace == "example-app"
    # auth rides the materialised kubeconfig
    assert rt.kubeconfig == "/tmp/kube/config"
    # usable as a context manager yielding the live URL
    with rt as runtime:
        assert runtime.target_url == "http://localhost:8080"


def test_kube_runtime_for_threads_port_forward_to_real_runtime() -> None:
    """_kube_runtime_for must set target.port_forward on the namespace it builds:
    the REAL KubernetesRuntime.start() reads it, and would AttributeError without
    it (the mocked runtime_cls path never hits start()). Live-found regression
    (#108)."""
    from agents.evaluator import _kube_runtime_for

    target = {
        "name": "web", "type": "kubernetes", "context": "kind-x",
        "namespace": "demo", "service": "web", "port": 8080, "port_forward": True,
    }
    rt = _kube_runtime_for(target)  # real KubernetesRuntime (no mock)
    assert rt is not None
    assert getattr(rt.target, "port_forward", None) is True
