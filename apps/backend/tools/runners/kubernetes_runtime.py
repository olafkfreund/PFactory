"""KubernetesRuntime — ``kubectl port-forward`` lifecycle for k8s targets (#108).

The k8s analog of :class:`tools.runners.app_runtime.AppRuntime`. A
``KubernetesTarget`` in ``.pfactory.yml`` names a cluster ``context`` /
``namespace`` / ``service`` / ``port``; this runtime port-forwards that service
to a local port for the duration of a test run and resolves the reachable URL
(``http://localhost:<port>``) that the Executor injects as
``PFACTORY_TARGET_URL``.

Auth (ServiceAccount token / mTLS client cert) is carried by the **kubeconfig**
that ``tools/runners/sandbox_credentials.py`` materialises for egress lanes and
mounts read-only; this runtime simply points ``kubectl`` at it via
``--kubeconfig`` + ``--context`` and never handles raw secrets itself.

The port-forward is a long-lived child process (``subprocess.Popen``), so —
unlike AppRuntime's one-shot compose calls — readiness is detected by reading
kubectl's ``Forwarding from 127.0.0.1:<port>`` line, and teardown terminates
the child. Used as a context manager so the forward is **always** torn down,
on success and on failure::

    from tools.runners.kubernetes_runtime import KubernetesRuntime

    with KubernetesRuntime(target, kubeconfig=kc_path) as rt:
        target_url = rt.target_url   # http://localhost:<port>
        # run the api/browser test against target_url …

Architecture notes:
  - ``kubectl_cmd`` is injectable (default ``("kubectl",)``).
  - ``popen_fn`` replaces ``subprocess.Popen`` in tests (no real kubectl).
  - ``clock`` replaces ``time.monotonic`` in tests (no real readiness waits).
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

# kubectl prints a forwarding line per bound loopback address once the tunnel is
# live, e.g. "Forwarding from 127.0.0.1:8080 -> 80" and/or
# "Forwarding from [::1]:8080 -> 80". The order is not guaranteed, and in some
# environments only the IPv6 line appears (e.g. when a stale forward still holds
# the IPv4 loopback port). We accept the first line of *either* family and grab
# its local port — ``target_url`` uses ``localhost``, which resolves to whichever
# was bound. Matching IPv4 only here used to hang on the next blocking readline
# when the [::1] line came first (#108).
_FORWARD_RE = re.compile(r"Forwarding from \S+?:(\d+)\s*->")


class KubernetesRuntimeError(Exception):
    """Raised when the port-forward cannot be started, become ready, or is misconfigured."""


@dataclass
class KubernetesRuntimeResult:
    """Outcome of :meth:`KubernetesRuntime.start`."""

    started: bool
    local_port: int | None
    target_url: str | None


class KubernetesRuntime:
    """Lifecycle wrapper around ``kubectl port-forward`` for one k8s target.

    Args:
        target: A ``KubernetesTarget`` from ``pfactory_yml.schema`` (needs
            ``context`` / ``namespace`` / ``service`` / ``port`` and
            ``port_forward=True``).
        kubeconfig: Path to the materialised kubeconfig (from
            ``sandbox_credentials``); passed as ``--kubeconfig``. ``None`` falls
            back to kubectl's ambient config (e.g. in-cluster).
        local_port: Local port to bind. Defaults to the target's remote port;
            pass ``0`` to let kubectl choose a free port (parsed from output).
        kubectl_cmd: Override the kubectl binary tuple (default ``("kubectl",)``).
        popen_fn: Replaces ``subprocess.Popen`` — injectable in tests.
        clock: Replaces ``time.monotonic`` — injectable in tests.
        readiness_timeout_seconds: How long to wait for the forwarding line.
    """

    def __init__(
        self,
        target,  # KubernetesTarget — not annotated to avoid a circular import
        *,
        kubeconfig: str | None = None,
        local_port: int | None = None,
        kubectl_cmd: tuple[str, ...] | None = None,
        popen_fn: Callable | None = None,
        clock: Callable[[], float] | None = None,
        readiness_timeout_seconds: float = 30.0,
    ) -> None:
        self.target = target
        self.kubeconfig = kubeconfig
        self._requested_local_port = local_port
        self.kubectl_cmd = kubectl_cmd or ("kubectl",)
        self._popen = popen_fn or subprocess.Popen
        self._clock = clock or time.monotonic
        self.readiness_timeout = readiness_timeout_seconds

        self._proc = None
        self._started = False
        self.local_port: int | None = None
        self.target_url: str | None = None

    # ── argv ────────────────────────────────────────────────────────────────

    def port_forward_argv(self) -> list[str]:
        """Build the ``kubectl port-forward`` command (pure).

        Raises:
            KubernetesRuntimeError: if ``service`` or ``port`` is missing.
        """
        t = self.target
        if not t.service:
            raise KubernetesRuntimeError(
                f"kubernetes target {t.name!r} requires 'service' to port-forward"
            )
        if not t.port:
            raise KubernetesRuntimeError(
                f"kubernetes target {t.name!r} requires 'port' to port-forward"
            )
        local = self._requested_local_port if self._requested_local_port is not None else t.port
        argv = list(self.kubectl_cmd)
        if self.kubeconfig:
            argv += ["--kubeconfig", str(self.kubeconfig)]
        argv += [
            "--context",
            t.context,
            "-n",
            t.namespace,
            "port-forward",
            f"service/{t.service}",
            f"{local}:{t.port}",
        ]
        return argv

    # ── start / stop ─────────────────────────────────────────────────────────

    def start(self) -> KubernetesRuntimeResult:
        """Launch ``kubectl port-forward`` and block until it is forwarding.

        Returns:
            ``KubernetesRuntimeResult`` with the resolved ``local_port`` and
            ``target_url``.

        Raises:
            KubernetesRuntimeError: if ``port_forward`` is disabled, the child
                exits before becoming ready, or readiness times out.
        """
        if not self.target.port_forward:
            raise KubernetesRuntimeError(
                f"kubernetes target {self.target.name!r} has port_forward=false; "
                "nothing to dispatch"
            )
        argv = self.port_forward_argv()
        self._proc = self._popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        local_port = self._await_ready()
        self.local_port = local_port
        self.target_url = f"http://localhost:{local_port}"
        self._started = True
        return KubernetesRuntimeResult(
            started=True, local_port=local_port, target_url=self.target_url
        )

    def _await_ready(self) -> int:
        """Read kubectl output until the forwarding line appears; return its port."""
        proc = self._proc
        deadline = self._clock() + self.readiness_timeout
        while self._clock() < deadline:
            if proc.poll() is not None:
                raise KubernetesRuntimeError(
                    f"kubectl port-forward exited early (code {proc.returncode}) "
                    "before it became ready — check context / namespace / service"
                )
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                # No output yet but still running — yield briefly and retry.
                time.sleep(0.05)
                continue
            match = _FORWARD_RE.search(line)
            if match:
                return int(match.group(1))
        raise KubernetesRuntimeError(
            f"kubectl port-forward did not become ready within {self.readiness_timeout}s"
        )

    def stop(self) -> None:
        """Terminate the port-forward child (idempotent; safe before start)."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - escalate to kill on any wait failure
                proc.kill()
        finally:
            self._proc = None
            self._started = False

    # ── context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> KubernetesRuntime:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Always tear the forward down — on success and on failure.
        self.stop()
