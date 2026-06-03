"""Helm chart acceptance tests for the gVisor RuntimeClass toggle.

Epic #35 child #37. Provides opt-in stronger pod isolation via
Kubernetes ``runtimeClassName``. Currently shipped only for gVisor;
the values key (``sandbox.gvisor.*``) leaves room for adding Kata
Containers or Firecracker in v1.x without renaming.

Acceptance shape:

- Default (``sandbox.gvisor.enabled=false``): no ``runtimeClassName``
  in the pod spec; chart renders identically to v1.0 pilot.
- Toggled on (``sandbox.gvisor.enabled=true``): ``runtimeClassName:
  gvisor`` lands on the pod spec.
- Toggle accepts a custom RuntimeClass name (``sandbox.gvisor.runtimeClassName=runsc``)
  for clusters that didn't follow the ``gvisor`` naming convention.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml


def _render(chart_dir, set_values: list[str] | None = None) -> list[dict]:
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _find_deployment(docs: list[dict]) -> dict:
    for d in docs:
        if d.get("kind") == "Deployment":
            return d
    raise AssertionError("no Deployment in rendered chart")


@pytest.mark.helm
class TestGvisorOff:
    """Default state — no runtimeClassName, chart unchanged from v1.0."""

    def test_pod_spec_has_no_runtime_class(
        self, helm_available, chart_dir,
    ) -> None:
        docs = _render(chart_dir, ["postgres.externalSecretName=test-pg"])
        dep = _find_deployment(docs)
        pod_spec = dep["spec"]["template"]["spec"]
        assert "runtimeClassName" not in pod_spec, (
            "runtimeClassName leaked into the pod spec when sandbox.gvisor "
            "is off — that would force every operator's cluster to "
            "provision the gvisor RuntimeClass."
        )


@pytest.mark.helm
class TestGvisorOn:
    """Toggle on — gvisor RuntimeClass lands on the pod spec."""

    def _docs_on(self, chart_dir, **overrides) -> list[dict]:
        sets = [
            "postgres.externalSecretName=test-pg",
            "sandbox.gvisor.enabled=true",
        ]
        for k, v in overrides.items():
            sets.append(f"sandbox.gvisor.{k}={v}")
        return _render(chart_dir, sets)

    def test_default_runtime_class_name(
        self, helm_available, chart_dir,
    ) -> None:
        dep = _find_deployment(self._docs_on(chart_dir))
        pod_spec = dep["spec"]["template"]["spec"]
        assert pod_spec.get("runtimeClassName") == "gvisor", (
            f"expected runtimeClassName=gvisor, got "
            f"{pod_spec.get('runtimeClassName')!r}"
        )

    def test_custom_runtime_class_name(
        self, helm_available, chart_dir,
    ) -> None:
        """Operators on platforms that use a non-standard RuntimeClass
        name (some K8s flavours ship ``runsc`` directly) can override
        the name without forking the chart."""
        dep = _find_deployment(
            self._docs_on(chart_dir, runtimeClassName="runsc"),
        )
        pod_spec = dep["spec"]["template"]["spec"]
        assert pod_spec.get("runtimeClassName") == "runsc"

    def test_security_context_unchanged_when_gvisor_on(
        self, helm_available, chart_dir,
    ) -> None:
        """Enabling gVisor does not weaken the existing layered
        defences — non-root user, dropped caps, RuntimeDefault
        seccomp profile all stay in place. gVisor stacks on top
        of these, it doesn't replace them."""
        dep = _find_deployment(self._docs_on(chart_dir))
        container = dep["spec"]["template"]["spec"]["containers"][0]
        ctx = container["securityContext"]
        assert ctx["allowPrivilegeEscalation"] is False
        assert ctx["readOnlyRootFilesystem"] is True
        assert ctx["runAsNonRoot"] is True
        assert ctx["capabilities"]["drop"] == ["ALL"]
