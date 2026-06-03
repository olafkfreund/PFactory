"""Sandbox credential injection gating (#73).

The unit lane (network=none) gets NO creds. Network-enabled lanes get
broker-resolved creds only when egress is opted in; a materialised kubeconfig
is mounted read-only and wiped after the run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.runners.sandbox_credentials import (
    SandboxCredentials,
    resolve_sandbox_credentials,
)


class _Status:
    def __init__(self, available: bool, env_vars: dict | None = None) -> None:
        self.available = available
        self.env_vars = env_vars or {}


class _FakeBroker:
    def __init__(self, per_provider: dict[str, _Status]) -> None:
        self._per_provider = per_provider
        self.closed = False

    def resolve_cloud(self, provider: str) -> _Status:
        return self._per_provider.get(provider, _Status(False))

    def close(self) -> None:
        self.closed = True


def test_unit_lane_gets_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # network="none" short-circuits before egress/broker are ever consulted.
    import pfactory_secrets.egress as egress_mod

    def _boom(*a, **k):  # would fail the test if called
        raise AssertionError("egress must not be consulted for the unit lane")

    monkeypatch.setattr(egress_mod, "egress_enabled", _boom)
    creds = resolve_sandbox_credentials(Path("/proj"), Path("/spec"), "none")
    assert creds.env == {} and creds.files == {} and creds.broker is None


def test_empty_when_egress_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import pfactory_secrets.egress as egress_mod

    monkeypatch.setattr(egress_mod, "egress_enabled", lambda *a, **k: False)
    creds = resolve_sandbox_credentials(Path("/proj"), Path("/spec"), "host")
    assert creds.env == {} and creds.files == {} and creds.broker is None


def test_network_lane_with_egress_gets_env_and_kubeconfig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kube = tmp_path / "kubeconfig"
    kube.write_text("apiVersion: v1\n")

    import pfactory_secrets.broker as broker_mod
    import pfactory_secrets.egress as egress_mod

    monkeypatch.setattr(egress_mod, "egress_enabled", lambda *a, **k: True)
    fake = _FakeBroker(
        {
            "aws": _Status(True, {"AWS_TOKEN": "tok"}),
            "kubernetes": _Status(True, {"KUBECONFIG": str(kube)}),
        }
    )
    monkeypatch.setattr(broker_mod, "CredentialBroker", lambda *a, **k: fake)

    creds = resolve_sandbox_credentials(tmp_path, tmp_path, "host")
    # token env present
    assert creds.env["AWS_TOKEN"] == "tok"
    # kubeconfig mounted read-only at the container path, KUBECONFIG repointed
    assert creds.files[str(kube.resolve())] == "/root/.kube/config"
    assert creds.env["KUBECONFIG"] == "/root/.kube/config"
    # wipe() erases via the broker
    creds.wipe()
    assert fake.closed is True
    assert creds.broker is None


def test_wipe_is_best_effort() -> None:
    class _Boom:
        def close(self) -> None:
            raise OSError("disk gone")

    creds = SandboxCredentials(broker=_Boom())
    creds.wipe()  # must not raise
    assert creds.broker is None


def test_resolution_failure_degrades_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import pfactory_secrets.egress as egress_mod

    monkeypatch.setattr(egress_mod, "egress_enabled", lambda *a, **k: True)

    import pfactory_secrets.broker as broker_mod

    def _raise(*a, **k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(broker_mod, "CredentialBroker", _raise)
    creds = resolve_sandbox_credentials(Path("/p"), Path("/s"), "host")
    assert creds.env == {} and creds.files == {} and creds.broker is None
