#!/usr/bin/env python3
"""
Cloud secrets-backend tests (epic #62, issues #66-#69): Vault, Azure Key Vault,
AWS Secrets Manager, GCP Secret Manager. Each SDK is injected as a fake module
via ``sys.modules`` so these run with or without the real packages installed.
"""

import sys
import types
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _fake_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    return mod


# ── Vault (#66) ─────────────────────────────────────────────────────────────

def test_vault_resolve_kv2_field(monkeypatch):
    from pfactory_secrets.backends.vault import VaultBackend
    from pfactory_secrets.refs import parse_ref

    class FakeClient:
        def __init__(self, url, token):  # noqa: D401
            self.url, self.token = url, token

        def read(self, path):
            return {"data": {"data": {"api_token": "tok-xyz", "other": "x"}}}

    hvac = _fake_module("hvac")
    hvac.Client = lambda url, token: FakeClient(url, token)
    monkeypatch.setitem(sys.modules, "hvac", hvac)
    monkeypatch.setenv("VAULT_ADDR", "https://vault.internal:8200")
    monkeypatch.setenv("VAULT_TOKEN", "s.token")

    b = VaultBackend()
    assert b.available()
    val = b.resolve(parse_ref("vault:secret/data/app#api_token"))
    assert val.value == "tok-xyz" and val.backend == "vault"


def test_vault_single_key_no_field(monkeypatch):
    from pfactory_secrets.backends.vault import VaultBackend
    from pfactory_secrets.refs import parse_ref

    hvac = _fake_module("hvac")
    hvac.Client = lambda url, token: type(
        "C", (), {"read": lambda self, p: {"data": {"only": "v"}}})()
    monkeypatch.setitem(sys.modules, "hvac", hvac)
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    assert VaultBackend().resolve(parse_ref("vault:kv/one")).value == "v"


def test_vault_egress_classification(monkeypatch):
    from pfactory_secrets import EgressClass
    from pfactory_secrets.backends.vault import VaultBackend

    assert VaultBackend(addr="https://127.0.0.1:8200").egress_class() is EgressClass.LOCAL
    assert VaultBackend(addr="https://vault.example.com").egress_class() is EgressClass.SELF_HOSTED


def test_vault_unavailable_without_addr(monkeypatch):
    from pfactory_secrets.backends.vault import VaultBackend

    monkeypatch.delenv("VAULT_ADDR", raising=False)
    assert VaultBackend(addr="").available() is False


def test_vault_missing_path(monkeypatch):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.vault import VaultBackend
    from pfactory_secrets.refs import parse_ref

    hvac = _fake_module("hvac")
    hvac.Client = lambda url, token: type("C", (), {"read": lambda self, p: None})()
    monkeypatch.setitem(sys.modules, "hvac", hvac)
    monkeypatch.setenv("VAULT_ADDR", "https://v")
    with pytest.raises(SecretNotFoundError):
        VaultBackend().resolve(parse_ref("vault:nope#k"))


# ── Azure Key Vault (#67) ───────────────────────────────────────────────────

def _install_fake_azure(monkeypatch, secret_value=None, raise_name=None):
    identity = _fake_module("azure.identity")
    identity.DefaultAzureCredential = lambda *a, **k: object()
    kvs = _fake_module("azure.keyvault.secrets")

    class FakeSecretClient:
        def __init__(self, vault_url, credential):
            self.vault_url = vault_url

        def get_secret(self, name):
            if raise_name:
                raise type(raise_name, (Exception,), {})()
            return type("S", (), {"value": secret_value})()

    kvs.SecretClient = FakeSecretClient
    # Parent packages must exist for ``import azure.identity`` to resolve.
    monkeypatch.setitem(sys.modules, "azure", _fake_module("azure"))
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.keyvault", _fake_module("azure.keyvault"))
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", kvs)


def test_azure_keyvault_resolve(monkeypatch):
    from pfactory_secrets import EgressClass
    from pfactory_secrets.backends.azure_keyvault import AzureKeyVaultBackend
    from pfactory_secrets.refs import parse_ref

    _install_fake_azure(monkeypatch, secret_value="azure-secret")
    b = AzureKeyVaultBackend()
    assert b.available() and b.egress_class() is EgressClass.MANAGED_CLOUD
    val = b.resolve(parse_ref("azurekv://my-vault/STAGING-TOKEN"))
    assert val.value == "azure-secret"
    assert val.source == "azurekv:my-vault/STAGING-TOKEN"


def test_azure_keyvault_not_found(monkeypatch):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.azure_keyvault import AzureKeyVaultBackend
    from pfactory_secrets.refs import parse_ref

    _install_fake_azure(monkeypatch, raise_name="ResourceNotFoundError")
    with pytest.raises(SecretNotFoundError):
        AzureKeyVaultBackend().resolve(parse_ref("azurekv://v/missing"))


# ── AWS Secrets Manager (#68) ───────────────────────────────────────────────

def _install_fake_boto3(monkeypatch, secret_string=None, error_code=None):
    boto3 = _fake_module("boto3")
    botocore = _fake_module("botocore")
    exc_mod = _fake_module("botocore.exceptions")

    class ClientError(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}

    class BotoCoreError(Exception):
        pass

    exc_mod.ClientError = ClientError
    exc_mod.BotoCoreError = BotoCoreError

    class FakeClient:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3 kwarg name
            if error_code:
                raise ClientError(error_code)
            return {"SecretString": secret_string}

    boto3.client = lambda service, region_name=None: FakeClient()
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exc_mod)


def test_aws_sm_plain_and_json(monkeypatch):
    from pfactory_secrets.backends.aws_secrets_manager import AwsSecretsManagerBackend
    from pfactory_secrets.refs import parse_ref

    _install_fake_boto3(monkeypatch, secret_string='{"token":"tok","user":"u"}')
    b = AwsSecretsManagerBackend(region="eu-west-1")
    assert b.available()
    assert b.resolve(parse_ref("aws-sm://staging/api#token")).value == "tok"

    _install_fake_boto3(monkeypatch, secret_string="plain-secret")
    assert AwsSecretsManagerBackend().resolve(parse_ref("aws-sm://plain")).value == "plain-secret"


def test_aws_sm_not_found(monkeypatch):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.aws_secrets_manager import AwsSecretsManagerBackend
    from pfactory_secrets.refs import parse_ref

    _install_fake_boto3(monkeypatch, error_code="ResourceNotFoundException")
    with pytest.raises(SecretNotFoundError):
        AwsSecretsManagerBackend().resolve(parse_ref("aws-sm://nope"))


# ── GCP Secret Manager (#69) ────────────────────────────────────────────────

def _install_fake_gcp(monkeypatch, payload=None, raise_name=None, recorder=None):
    google = _fake_module("google")
    cloud = _fake_module("google.cloud")
    sm = _fake_module("google.cloud.secretmanager")

    class FakeClient:
        def access_secret_version(self, name):
            if recorder is not None:
                recorder["name"] = name
            if raise_name:
                raise type(raise_name, (Exception,), {})()
            return type("R", (), {"payload": type("P", (), {"data": payload})()})()

    sm.SecretManagerServiceClient = FakeClient
    cloud.secretmanager = sm
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", sm)


def test_gcp_sm_resolve_default_version(monkeypatch):
    from pfactory_secrets import EgressClass
    from pfactory_secrets.backends.gcp_secret_manager import GcpSecretManagerBackend
    from pfactory_secrets.refs import parse_ref

    rec: dict = {}
    _install_fake_gcp(monkeypatch, payload=b"gcp-secret", recorder=rec)
    b = GcpSecretManagerBackend()
    assert b.available() and b.egress_class() is EgressClass.MANAGED_CLOUD
    val = b.resolve(parse_ref("gcp-sm://my-proj/db-pass"))
    assert val.value == "gcp-secret"
    assert rec["name"] == "projects/my-proj/secrets/db-pass/versions/latest"


def test_gcp_sm_pinned_version(monkeypatch):
    from pfactory_secrets.backends.gcp_secret_manager import GcpSecretManagerBackend
    from pfactory_secrets.refs import parse_ref

    rec: dict = {}
    _install_fake_gcp(monkeypatch, payload=b"v3", recorder=rec)
    GcpSecretManagerBackend().resolve(parse_ref("gcp-sm://proj/sec/7"))
    assert rec["name"].endswith("/versions/7")


def test_gcp_sm_not_found(monkeypatch):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.gcp_secret_manager import GcpSecretManagerBackend
    from pfactory_secrets.refs import parse_ref

    _install_fake_gcp(monkeypatch, raise_name="NotFound")
    with pytest.raises(SecretNotFoundError):
        GcpSecretManagerBackend().resolve(parse_ref("gcp-sm://proj/missing"))


# ── factory wiring ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("alias,cls", [
    ("vault", "VaultBackend"),
    ("azurekv", "AzureKeyVaultBackend"),
    ("aws-sm", "AwsSecretsManagerBackend"),
    ("gcp-sm", "GcpSecretManagerBackend"),
])
def test_factory_routes_to_cloud_backends(alias, cls):
    from pfactory_secrets.factory import get_secrets_backend

    assert type(get_secrets_backend(alias)).__name__ == cls
