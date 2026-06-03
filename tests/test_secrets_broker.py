#!/usr/bin/env python3
"""
CredentialBroker tests (epic #62, issue #65): ref resolution + egress gating,
cloud resolution (backend-fetch head + ambient fallback), ephemeral file
materialisation (0600 + wiped on close).
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture
def cloud_config(monkeypatch, tmp_path):
    """Write a temp ~/.pfactory/credentials.json and point the broker at it."""
    import pfactory_secrets.broker as broker

    def _write(mapping: dict):
        cfg = tmp_path / "credentials.json"
        cfg.write_text(json.dumps({"cloud": mapping}))
        cfg.chmod(0o600)
        monkeypatch.setattr(broker, "CREDENTIALS_CONFIG_PATH", cfg)
        broker.reset_config_cache()
        return cfg

    yield _write
    broker.reset_config_cache()


# ── resolve_ref + egress gate ───────────────────────────────────────────────

def test_resolve_ref_local_backend_always_allowed(monkeypatch):
    from pfactory_secrets.broker import CredentialBroker

    monkeypatch.setenv("BROKER_LOCAL_SECRET", "hello")
    with CredentialBroker(egress_allowed=False) as b:  # egress OFF
        val = b.resolve_ref("env:BROKER_LOCAL_SECRET")
    assert val.value == "hello"  # env is LOCAL -> no egress needed


def test_resolve_ref_nonlocal_blocked_when_egress_off(monkeypatch):
    import pfactory_secrets.factory as factory
    from pfactory_secrets import EgressClass, SecretsError, SecretValue
    from pfactory_secrets.broker import CredentialBroker

    class FakeCloud:
        def egress_class(self):
            return EgressClass.MANAGED_CLOUD

        def resolve(self, ref):
            return SecretValue(value="v", backend="fake", ref=ref.raw)

    monkeypatch.setattr(factory, "get_secrets_backend", lambda name, **k: FakeCloud())

    with CredentialBroker(egress_allowed=False) as b:
        with pytest.raises(SecretsError, match="egress is not enabled"):
            b.resolve_ref("vault:secret/data/x#token")

    with CredentialBroker(egress_allowed=True) as b:  # egress ON -> allowed
        assert b.resolve_ref("vault:secret/data/x#token").value == "v"


# ── resolve_cloud ───────────────────────────────────────────────────────────

def test_resolve_cloud_disabled_by_default():
    from pfactory_secrets.broker import CredentialBroker

    with CredentialBroker(egress_allowed=False) as b:
        status = b.resolve_cloud("gcp")
    assert not status.available and status.source == "egress-disabled"


def test_resolve_cloud_unknown_provider():
    from pfactory_secrets.broker import CredentialBroker

    with CredentialBroker(egress_allowed=True) as b:
        assert not b.resolve_cloud("digitalocean").available


def test_resolve_cloud_materialises_file_cred(monkeypatch, cloud_config, tmp_path):
    from pfactory_secrets.broker import CredentialBroker

    monkeypatch.setenv("FAKE_GCP_JSON", '{"type":"service_account","x":1}')
    cloud_config({
        "gcp": {"ref": "env:FAKE_GCP_JSON",
                "as": "GOOGLE_APPLICATION_CREDENTIALS", "kind": "file"},
    })

    b = CredentialBroker(spec_dir=tmp_path, egress_allowed=True)
    status = b.resolve_cloud("gcp")
    assert status.available
    cred_path = Path(status.env_vars["GOOGLE_APPLICATION_CREDENTIALS"])
    assert cred_path.is_file()
    assert json.loads(cred_path.read_text())["type"] == "service_account"
    # 0600
    assert stat.S_IMODE(cred_path.stat().st_mode) == 0o600

    b.close()
    assert not cred_path.exists()  # wiped


def test_resolve_cloud_env_kind(monkeypatch, cloud_config, tmp_path):
    from pfactory_secrets.broker import CredentialBroker

    monkeypatch.setenv("STAGING_TOKEN", "tok-123")
    cloud_config({"aws": {"ref": "env:STAGING_TOKEN", "as": "AWS_SESSION_TOKEN"}})

    with CredentialBroker(spec_dir=tmp_path, egress_allowed=True) as b:
        status = b.resolve_cloud("aws")
        assert status.available
        assert status.env_vars["AWS_SESSION_TOKEN"] == "tok-123"
        assert b.materialised_env()["AWS_SESSION_TOKEN"] == "tok-123"


def test_resolve_cloud_falls_back_to_ambient(monkeypatch, cloud_config, tmp_path):
    """No backend ref configured -> defer to core.mcp_credentials."""
    import core.mcp_credentials as mc
    from core.mcp_credentials import CredentialStatus
    from pfactory_secrets.broker import CredentialBroker

    cloud_config({})  # empty
    monkeypatch.setattr(
        mc, "get_credential_status",
        lambda p: CredentialStatus(True, "ambient:test", {"X": "1"}),
    )
    with CredentialBroker(spec_dir=tmp_path, egress_allowed=True) as b:
        status = b.resolve_cloud("kubernetes")
    assert status.available and status.source == "ambient:test"


def test_backend_ref_failure_falls_back(monkeypatch, cloud_config, tmp_path):
    """A broken backend ref must not crash resolve_cloud — fall back to ambient."""
    import core.mcp_credentials as mc
    from core.mcp_credentials import CredentialStatus
    from pfactory_secrets.broker import CredentialBroker

    # ref points at an unset env var -> SecretNotFoundError inside resolve_ref.
    monkeypatch.delenv("MISSING_REF_VAR", raising=False)
    cloud_config({"azure": {"ref": "env:MISSING_REF_VAR", "as": "AZURE_TOKEN"}})
    monkeypatch.setattr(
        mc, "get_credential_status",
        lambda p: CredentialStatus(True, "ambient:fallback"),
    )
    with CredentialBroker(spec_dir=tmp_path, egress_allowed=True) as b:
        status = b.resolve_cloud("azure")
    assert status.available and status.source == "ambient:fallback"


# ── materialise + wipe ──────────────────────────────────────────────────────

def test_materialise_file_mode_and_wipe(tmp_path):
    from pfactory_secrets.broker import CredentialBroker

    b = CredentialBroker(spec_dir=tmp_path, egress_allowed=True)
    p = b.materialise_file("kubeconfig", "apiVersion: v1\n")
    assert p.is_file() and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert p.read_text().startswith("apiVersion")
    b.close()
    assert not p.exists()


def test_inject_task_credentials_noop_by_default(monkeypatch, cloud_config, tmp_path):
    from pfactory_secrets.broker import inject_task_credentials

    monkeypatch.delenv("PFACTORY_EGRESS_ENABLED", raising=False)
    monkeypatch.setenv("TK2", "v")
    cloud_config({"aws": {"ref": "env:TK2", "as": "AWS_SESSION_TOKEN"}})
    env = {"A": "1"}
    out = inject_task_credentials(env, spec_dir=tmp_path)
    assert out == {"A": "1"}  # egress off -> nothing injected


def test_inject_task_credentials_when_enabled(monkeypatch, cloud_config, tmp_path):
    from pfactory_secrets.broker import inject_task_credentials

    monkeypatch.setenv("PFACTORY_EGRESS_ENABLED", "1")
    monkeypatch.setenv("TK3", "tok")
    cloud_config({"aws": {"ref": "env:TK3", "as": "AWS_SESSION_TOKEN"}})
    env: dict[str, str] = {}
    inject_task_credentials(env, spec_dir=tmp_path)
    assert env["AWS_SESSION_TOKEN"] == "tok"


def test_inject_task_credentials_never_raises(monkeypatch, tmp_path):
    from pfactory_secrets.broker import inject_task_credentials

    monkeypatch.setenv("PFACTORY_EGRESS_ENABLED", "true")
    # No config file at all -> ambient fallback may run; must not raise.
    env: dict[str, str] = {}
    assert inject_task_credentials(env, spec_dir=tmp_path) is env


def test_apply_to_env_merges(monkeypatch, cloud_config, tmp_path):
    from pfactory_secrets.broker import CredentialBroker

    monkeypatch.setenv("TK", "v")
    cloud_config({"aws": {"ref": "env:TK", "as": "AWS_SESSION_TOKEN"}})
    with CredentialBroker(spec_dir=tmp_path, egress_allowed=True) as b:
        b.resolve_cloud("aws")
        env = {"EXISTING": "1"}
        b.apply_to_env(env)
        assert env["EXISTING"] == "1" and env["AWS_SESSION_TOKEN"] == "v"
