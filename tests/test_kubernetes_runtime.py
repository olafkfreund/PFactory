"""Tests for KubernetesRuntime + dispatch_kubernetes_lane (#108).

Backend-pure: ``subprocess.Popen`` is replaced by an injected ``popen_fn`` and
the clock is injectable, so no real ``kubectl`` or cluster is touched.
"""

from __future__ import annotations

import pytest
from pfactory_yml.schema import KubernetesTarget, ServiceAccountAuth
from tools.runners.kubernetes_runtime import (
    KubernetesRuntime,
    KubernetesRuntimeError,
)
from tools.runners.lane_dispatch import dispatch_kubernetes_lane

# ── helpers ──────────────────────────────────────────────────────────────────


def _target(
    *,
    context: str = "prod-readonly",
    namespace: str = "example-app",
    service: str | None = "billing",
    port: int | None = 8080,
    port_forward: bool = True,
) -> KubernetesTarget:
    return KubernetesTarget(
        type="kubernetes",
        name="cluster",
        context=context,
        namespace=namespace,
        auth=ServiceAccountAuth(type="serviceaccount", token_file="/var/run/sa/token"),
        service=service,
        port=port,
        port_forward=port_forward,
    )


class _FakeProc:
    """Stand-in for the ``kubectl port-forward`` Popen child."""

    def __init__(self, lines: list[str], exit_code: int | None = None) -> None:
        self._lines = list(lines)
        self._i = 0
        self._exit = exit_code  # None = still running
        self.returncode = exit_code
        self.terminated = False
        self.killed = False
        self.stdout = self  # readline() lives on the proc for simplicity

    def readline(self) -> str:
        if self._i < len(self._lines):
            line = self._lines[self._i]
            self._i += 1
            return line
        return ""

    def poll(self) -> int | None:
        return self._exit

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True


def _popen_for(proc: _FakeProc):
    captured: dict = {}

    def _popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return proc

    return _popen, captured


# ── argv (pure) ──────────────────────────────────────────────────────────────


def test_port_forward_argv_with_kubeconfig() -> None:
    rt = KubernetesRuntime(_target(port=8080), kubeconfig="/kc/config")
    assert rt.port_forward_argv() == [
        "kubectl", "--kubeconfig", "/kc/config",
        "--context", "prod-readonly", "-n", "example-app",
        "port-forward", "service/billing", "8080:8080",
    ]


def test_port_forward_argv_local_port_override_no_kubeconfig() -> None:
    rt = KubernetesRuntime(_target(port=80), local_port=9000)
    argv = rt.port_forward_argv()
    assert "--kubeconfig" not in argv
    assert argv[-1] == "9000:80"


def test_port_forward_argv_requires_service() -> None:
    rt = KubernetesRuntime(_target(service=None))
    with pytest.raises(KubernetesRuntimeError, match="requires 'service'"):
        rt.port_forward_argv()


def test_port_forward_argv_requires_port() -> None:
    rt = KubernetesRuntime(_target(port=None))
    with pytest.raises(KubernetesRuntimeError, match="requires 'port'"):
        rt.port_forward_argv()


# ── start / readiness ────────────────────────────────────────────────────────


def test_start_resolves_target_url_from_forwarding_line() -> None:
    proc = _FakeProc(["Forwarding from 127.0.0.1:8080 -> 8080\n"])
    popen, captured = _popen_for(proc)
    rt = KubernetesRuntime(_target(port=8080), kubeconfig="/kc", popen_fn=popen)
    res = rt.start()
    assert res.started is True
    assert res.local_port == 8080
    assert rt.target_url == "http://localhost:8080"
    # the [::1] line is ignored; we bind loopback only
    assert "port-forward" in captured["argv"]


def test_start_parses_kubectl_chosen_port() -> None:
    # local_port=0 → kubectl picks a free port; we must read it from the output.
    proc = _FakeProc([
        "Forwarding from 127.0.0.1:54321 -> 8080\n",
        "Forwarding from [::1]:54321 -> 8080\n",
    ])
    popen, _ = _popen_for(proc)
    rt = KubernetesRuntime(_target(port=8080), local_port=0, popen_fn=popen)
    rt.start()
    assert rt.local_port == 54321
    assert rt.target_url == "http://localhost:54321"


def test_start_raises_when_port_forward_disabled() -> None:
    rt = KubernetesRuntime(_target(port_forward=False), popen_fn=_popen_for(_FakeProc([]))[0])
    with pytest.raises(KubernetesRuntimeError, match="port_forward=false"):
        rt.start()


def test_start_raises_when_process_exits_early() -> None:
    proc = _FakeProc([], exit_code=1)
    popen, _ = _popen_for(proc)
    rt = KubernetesRuntime(_target(), popen_fn=popen)
    with pytest.raises(KubernetesRuntimeError, match="exited early"):
        rt.start()


def test_start_raises_on_readiness_timeout() -> None:
    proc = _FakeProc(["waiting...\n"], exit_code=None)
    clocks = iter([0.0, 100.0, 200.0])
    popen, _ = _popen_for(proc)
    rt = KubernetesRuntime(
        _target(), popen_fn=popen, clock=lambda: next(clocks),
        readiness_timeout_seconds=10.0,
    )
    with pytest.raises(KubernetesRuntimeError, match="did not become ready"):
        rt.start()


# ── teardown ─────────────────────────────────────────────────────────────────


def test_stop_terminates_process() -> None:
    proc = _FakeProc(["Forwarding from 127.0.0.1:8080 -> 8080\n"])
    rt = KubernetesRuntime(_target(), popen_fn=_popen_for(proc)[0])
    rt.start()
    rt.stop()
    assert proc.terminated is True


def test_context_manager_tears_down_on_exception() -> None:
    proc = _FakeProc(["Forwarding from 127.0.0.1:8080 -> 8080\n"])
    rt = KubernetesRuntime(_target(), popen_fn=_popen_for(proc)[0])
    with pytest.raises(RuntimeError):
        with rt as r:
            assert r.target_url == "http://localhost:8080"
            raise RuntimeError("boom")
    # The forward is torn down even though the body raised.
    assert proc.terminated is True


def test_stop_is_safe_before_start() -> None:
    rt = KubernetesRuntime(_target())
    rt.stop()  # no proc yet — must be a no-op, not an error


# ── dispatch_kubernetes_lane ─────────────────────────────────────────────────


class _FakeRuntime:
    instances: list = []

    def __init__(self, target, *, kubeconfig=None) -> None:
        self.target = target
        self.kubeconfig = kubeconfig
        self.entered = False
        self.exited = False
        self.target_url = "http://localhost:7777"
        _FakeRuntime.instances.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True


class _FakeRunner:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def run(self, **kwargs):
        self.kwargs = kwargs
        return "RUN_RESULT"


def test_dispatch_kubernetes_lane_injects_target_url_and_tears_down() -> None:
    _FakeRuntime.instances.clear()
    runner = _FakeRunner()
    res = dispatch_kubernetes_lane(
        lane="api",
        target=_target(),
        docker_runner=runner,  # type: ignore[arg-type]
        docker_run_kwargs={
            "repo_path": "/r",
            "scratch_path": "/s",
            "command": ["pytest"],
            "extra_env": {"FOO": "bar"},
        },
        kubeconfig="/kc/config",
        kube_runtime_cls=_FakeRuntime,
    )
    assert res.lane == "api"
    assert res.runner_used == "docker"
    assert res.docker_result == "RUN_RESULT"
    # PFACTORY_TARGET_URL injected, caller env preserved
    assert runner.kwargs["extra_env"]["PFACTORY_TARGET_URL"] == "http://localhost:7777"
    assert runner.kwargs["extra_env"]["FOO"] == "bar"
    # runtime entered + torn down, kubeconfig threaded through
    inst = _FakeRuntime.instances[-1]
    assert inst.entered and inst.exited
    assert inst.kubeconfig == "/kc/config"


def test_start_resolves_from_ipv6_forwarding_line_first() -> None:
    """kubectl may print the [::1] line before (or instead of) the 127.0.0.1
    line — the runtime must resolve from either family, not hang waiting for
    IPv4. Live-found regression (#108)."""
    proc = _FakeProc(["Forwarding from [::1]:8080 -> 8080\n"])
    popen, _ = _popen_for(proc)
    rt = KubernetesRuntime(_target(port=8080), popen_fn=popen)
    res = rt.start()
    assert res.started is True
    assert res.local_port == 8080
    assert rt.target_url == "http://localhost:8080"
