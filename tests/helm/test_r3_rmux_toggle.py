"""Helm chart acceptance tests for Epic #44 R3 — rmux toggle.

When ``rmux.enabled=false`` (default):
  • No ``PFACTORY_RMUX_ENABLED`` env var present
  • No ``pfactory-rmux`` or ``pfactory-panes`` volumes
  • Replica count honours ``replicaCount`` (default 1, but operator-settable)

When ``rmux.enabled=true``:
  • ``PFACTORY_RMUX_ENABLED=true`` env var injected
  • ``pfactory-rmux`` (10Mi tmpfs) + ``pfactory-panes`` (100Mi tmpfs)
    volumes mounted at the well-known paths
  • Replicas pinned to 1 regardless of ``replicaCount``
"""

from __future__ import annotations

import subprocess

import pytest
import yaml


def _render(chart_dir, set_values: list[str] | None = None) -> list[dict]:
    """``helm template`` the chart with optional ``--set`` overrides.

    Returns the rendered manifests parsed as Python dicts.  Filters
    out empty docs produced by conditional templates.
    """
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    return docs


def _find_deployment(docs: list[dict]) -> dict:
    for d in docs:
        if d.get("kind") == "Deployment":
            return d
    raise AssertionError("no Deployment in rendered chart")


@pytest.mark.helm
class TestRmuxToggleOff:
    """Default state — bank-pilot image compatible, no rmux surface."""

    def test_no_rmux_env_var(self, helm_available, chart_dir) -> None:
        docs = _render(chart_dir, ["postgres.externalSecretName=test-pg"])
        dep = _find_deployment(docs)
        container = dep["spec"]["template"]["spec"]["containers"][0]
        env_names = {e["name"] for e in container.get("env", [])}
        assert "PFACTORY_RMUX_ENABLED" not in env_names

    def test_no_rmux_volumes(self, helm_available, chart_dir) -> None:
        docs = _render(chart_dir, ["postgres.externalSecretName=test-pg"])
        dep = _find_deployment(docs)
        volume_names = {v["name"] for v in dep["spec"]["template"]["spec"]["volumes"]}
        assert "pfactory-rmux" not in volume_names
        assert "pfactory-panes" not in volume_names

    def test_honours_replica_count(self, helm_available, chart_dir) -> None:
        docs = _render(
            chart_dir,
            ["postgres.externalSecretName=test-pg", "replicaCount=3"],
        )
        dep = _find_deployment(docs)
        # rmux off → replicaCount wins
        assert dep["spec"]["replicas"] == 3


@pytest.mark.helm
class TestRmuxToggleOn:
    """``rmux.enabled=true`` flips the three operator-facing surfaces."""

    def test_env_var_injected(self, helm_available, chart_dir) -> None:
        docs = _render(
            chart_dir,
            ["postgres.externalSecretName=test-pg", "rmux.enabled=true"],
        )
        dep = _find_deployment(docs)
        container = dep["spec"]["template"]["spec"]["containers"][0]
        env = {e["name"]: e.get("value") for e in container.get("env", []) if "value" in e}
        assert env.get("PFACTORY_RMUX_ENABLED") == "true"

    def test_volumes_mounted_at_expected_paths(
        self, helm_available, chart_dir
    ) -> None:
        docs = _render(
            chart_dir,
            ["postgres.externalSecretName=test-pg", "rmux.enabled=true"],
        )
        dep = _find_deployment(docs)
        spec = dep["spec"]["template"]["spec"]
        volume_names = {v["name"] for v in spec["volumes"]}
        assert "pfactory-rmux" in volume_names
        assert "pfactory-panes" in volume_names

        mounts = {
            m["name"]: m["mountPath"]
            for m in spec["containers"][0]["volumeMounts"]
        }
        assert mounts.get("pfactory-rmux") == "/var/run/pfactory/rmux"
        assert mounts.get("pfactory-panes") == "/var/run/pfactory/panes"

    def test_volumes_are_tmpfs_with_size_limit(
        self, helm_available, chart_dir
    ) -> None:
        """Volumes must be ``medium: Memory`` (tmpfs) for low-latency
        pipe-pane I/O, with sizeLimit set for capacity protection."""
        docs = _render(
            chart_dir,
            ["postgres.externalSecretName=test-pg", "rmux.enabled=true"],
        )
        dep = _find_deployment(docs)
        volumes = {v["name"]: v for v in dep["spec"]["template"]["spec"]["volumes"]}
        for vname in ("pfactory-rmux", "pfactory-panes"):
            ed = volumes[vname]["emptyDir"]
            assert ed.get("medium") == "Memory", f"{vname} must be tmpfs"
            assert "sizeLimit" in ed, f"{vname} must have a sizeLimit"

    def test_replicas_pinned_to_1_even_with_higher_replica_count(
        self, helm_available, chart_dir
    ) -> None:
        """Multi-replica rmux is v1.1 (Redis pub/sub).  The chart MUST
        override ``replicaCount`` when rmux is on so an operator who
        cranks it up doesn't silently break cross-replica session
        visibility."""
        docs = _render(
            chart_dir,
            [
                "postgres.externalSecretName=test-pg",
                "rmux.enabled=true",
                "replicaCount=5",
            ],
        )
        dep = _find_deployment(docs)
        assert dep["spec"]["replicas"] == 1
