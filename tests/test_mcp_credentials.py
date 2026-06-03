"""Unit tests for the MCP credentials probe matrix.

Covers the priority chain (operator config → env vars → CLI files → in-cluster)
per provider, plus a few cross-cutting guarantees (permission refusal on the
operator config, unknown providers, empty values).

These probes are pure filesystem/env checks — no API calls — so we drive them
with ``monkeypatch`` + ``tmp_path``. The frozen ``CredentialStatus`` and the
``lru_cache`` on ``_load_operator_config`` mean each test must call
``reset_cache()`` to avoid leakage between cases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from core import mcp_credentials as mc


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Each test gets a clean $HOME and a clean operator-config cache.

    Without this, tests pick up the developer's real ~/.aws/credentials or
    ~/.kube/config and start passing/failing for reasons unrelated to the
    code under test.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-point the module-level path to the new HOME
    monkeypatch.setattr(mc, "OPERATOR_CONFIG_PATH", tmp_path / ".pfactory" / "mcp-credentials.json")
    # K8s in-cluster token path — point at a definitely-non-existent file
    monkeypatch.setattr(
        mc,
        "K8S_SERVICE_ACCOUNT_TOKEN",
        tmp_path / "definitely-not-a-token",
    )
    # Wipe any cloud-cred env vars the host might leak in
    for var in (
        "GITHUB_TOKEN",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "GITLAB_API_TOKEN",
        "GITLAB_INSTANCE_URL",
        "AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN",
        "AZURE_DEVOPS_ORG_SERVICE_URL",
        "KUBECONFIG",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_USE_MSI",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(var, raising=False)
    mc.reset_cache()
    yield
    mc.reset_cache()


def _write_operator_config(tmp_path: Path, payload: dict) -> Path:
    """Write a 0600 ~/.pfactory/mcp-credentials.json and return its path."""
    target = tmp_path / ".pfactory" / "mcp-credentials.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload))
    target.chmod(0o600)
    return target


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_unknown_provider_returns_unavailable():
    status = mc.get_credential_status("imaginary-cloud")
    assert status.available is False
    assert "unknown-provider" in status.source


def test_operator_config_refused_with_loose_perms(tmp_path, monkeypatch):
    cfg = _write_operator_config(tmp_path, {"github": {"tokenEnv": "MY_TOKEN"}})
    cfg.chmod(0o644)  # loosen perms
    monkeypatch.setenv("MY_TOKEN", "ghp_abc")
    mc.reset_cache()
    # With config refused, falls through to env probes — but only the well-known
    # vars are checked, not "MY_TOKEN", so we still get unavailable.
    status = mc.get_credential_status("github")
    assert status.available is False


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def test_github_picks_up_GITHUB_TOKEN(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_aaa")
    s = mc.get_credential_status("github")
    assert s.available
    assert s.source == "env:GITHUB_TOKEN"
    assert s.env_vars["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_aaa"


def test_github_picks_up_GH_TOKEN_fallback(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_zzz")
    s = mc.get_credential_status("github")
    assert s.available
    assert s.source == "env:GH_TOKEN"


def test_github_operator_config_indirects_to_named_env(tmp_path, monkeypatch):
    _write_operator_config(tmp_path, {"github": {"tokenEnv": "GH_ROBOT_TOKEN"}})
    monkeypatch.setenv("GH_ROBOT_TOKEN", "ghp_robot")
    mc.reset_cache()
    s = mc.get_credential_status("github")
    assert s.available
    assert s.source == "env:GH_ROBOT_TOKEN"
    assert s.env_vars["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_robot"


def test_github_no_token_is_unavailable():
    s = mc.get_credential_status("github")
    assert s.available is False


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


def test_kubernetes_picks_up_default_kubeconfig(tmp_path):
    (tmp_path / ".kube").mkdir()
    (tmp_path / ".kube" / "config").write_text("apiVersion: v1\nkind: Config\n")
    s = mc.get_credential_status("kubernetes")
    assert s.available
    assert "~/.kube/config" in s.source


def test_kubernetes_picks_up_KUBECONFIG_env(tmp_path, monkeypatch):
    kc = tmp_path / "custom-kubeconfig.yaml"
    kc.write_text("apiVersion: v1\nkind: Config\n")
    monkeypatch.setenv("KUBECONFIG", str(kc))
    s = mc.get_credential_status("kubernetes")
    assert s.available
    assert s.source == "env:KUBECONFIG"
    assert s.env_vars["KUBECONFIG"] == str(kc)


def test_kubernetes_in_cluster_token(tmp_path, monkeypatch):
    token = tmp_path / "k8s-sa-token"
    token.write_text("eyJhbG...")
    monkeypatch.setattr(mc, "K8S_SERVICE_ACCOUNT_TOKEN", token)
    s = mc.get_credential_status("kubernetes")
    assert s.available
    assert s.source == "k8s-in-cluster"


def test_kubernetes_nothing_present_is_unavailable():
    s = mc.get_credential_status("kubernetes")
    assert s.available is False


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------


def test_aws_picks_up_access_keys(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA...")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret...")
    s = mc.get_credential_status("aws")
    assert s.available
    assert s.source == "env:AWS_ACCESS_KEY_ID"


def test_aws_default_credentials_file(tmp_path):
    (tmp_path / ".aws").mkdir()
    (tmp_path / ".aws" / "credentials").write_text("[default]\naws_access_key_id=xxx\n")
    s = mc.get_credential_status("aws")
    assert s.available
    assert s.source == "file:~/.aws/credentials"


def test_aws_irsa_detection(monkeypatch):
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123:role/x")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", "/var/run/secrets/x")
    s = mc.get_credential_status("aws")
    assert s.available
    assert s.source == "irsa"


def test_aws_nothing_present_is_unavailable():
    s = mc.get_credential_status("aws")
    assert s.available is False


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------


def test_azure_service_principal_env_triplet(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    s = mc.get_credential_status("azure")
    assert s.available
    assert s.source == "service-principal"
    assert s.env_vars["AZURE_TENANT_ID"] == "t"


def test_azure_picks_up_cli_cache(tmp_path):
    (tmp_path / ".azure").mkdir()
    (tmp_path / ".azure" / "azureProfile.json").write_text("{}")
    s = mc.get_credential_status("azure")
    assert s.available
    assert "azureProfile.json" in s.source


def test_azure_managed_identity_signal(monkeypatch):
    monkeypatch.setenv("AZURE_USE_MSI", "true")
    s = mc.get_credential_status("azure")
    assert s.available
    assert s.source == "managed-identity"


def test_azure_partial_sp_is_unavailable(monkeypatch):
    # Missing AZURE_CLIENT_SECRET ⇒ shouldn't claim sp creds
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    s = mc.get_credential_status("azure")
    assert s.available is False


# ---------------------------------------------------------------------------
# GCP / GitLab / ADO smoke
# ---------------------------------------------------------------------------


def test_gcp_picks_up_GOOGLE_APPLICATION_CREDENTIALS(tmp_path, monkeypatch):
    sa = tmp_path / "sa.json"
    sa.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(sa))
    s = mc.get_credential_status("gcp")
    assert s.available
    assert str(sa) in s.source
    assert s.env_vars["GOOGLE_APPLICATION_CREDENTIALS"] == str(sa)


def test_gcp_adc_default_file(tmp_path):
    adc = tmp_path / ".config" / "gcloud" / "application_default_credentials.json"
    adc.parent.mkdir(parents=True)
    adc.write_text("{}")
    s = mc.get_credential_status("gcp")
    assert s.available


def test_gitlab_requires_token(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-x")
    s = mc.get_credential_status("gitlab")
    assert s.available
    assert s.env_vars["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-x"


def test_azure_devops_requires_token_AND_url(monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN", "ado")
    # Missing org url — still unavailable
    assert mc.get_credential_status("azure_devops").available is False
    monkeypatch.setenv("AZURE_DEVOPS_ORG_SERVICE_URL", "https://dev.azure.com/o")
    s = mc.get_credential_status("azure_devops")
    assert s.available
    assert s.env_vars["AZURE_DEVOPS_ORG_SERVICE_URL"].startswith("https://")
