#!/usr/bin/env python3
"""
Foundation tests for the pfactory_secrets package (epic #62, issue #63):
ref parsing/routing, the factory (aliases + lazy + not-yet-implemented),
the env + localfile backends, and value redaction.
"""

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ── ref routing (mirrors tests/test_studio_routing.py) ──────────────────────

@pytest.mark.parametrize("ref,backend", [
    ("env:STAGING_API_TOKEN", "env"),
    ("file:/run/secrets/token", "localfile"),
    ("sops:secrets.enc.yaml#api_token", "localfile"),
    ("agenix:staging-token.age", "localfile"),
    ("vault:secret/data/pfactory/staging#api_token", "vault"),
    ("azurekv://my-vault/STAGING-API-TOKEN", "azure_keyvault"),
    ("aws-sm://staging/api#token", "aws_secrets_manager"),
    ("gcp-sm://my-project/staging-api-token", "gcp_secret_manager"),
])
def test_infer_backend_from_ref(ref, backend):
    from pfactory_secrets.refs import infer_backend_from_ref

    assert infer_backend_from_ref(ref) == backend


def test_parse_ref_locator_family():
    from pfactory_secrets.refs import parse_ref

    r = parse_ref("vault:secret/data/app#token")
    assert (r.backend, r.locator, r.field) == ("vault", "secret/data/app", "token")
    assert parse_ref("env:TOK").field is None
    assert parse_ref("sops:s.yaml#k").extra["format"] == "sops"


def test_parse_ref_authority_family():
    from pfactory_secrets.refs import parse_ref

    az = parse_ref("azurekv://my-vault/NAME")
    assert az.extra["vault"] == "my-vault" and az.locator == "NAME"

    aws = parse_ref("aws-sm://staging/api#token")
    assert aws.locator == "staging/api" and aws.field == "token"

    gcp = parse_ref("gcp-sm://proj/sec/7")
    assert gcp.extra["project"] == "proj" and gcp.locator == "sec" and gcp.version == "7"
    assert parse_ref("gcp-sm://proj/sec").version is None


@pytest.mark.parametrize("bad", [
    "", "noscheme", "bogus:whatever", "env:", "azurekv://onlyvault",
    "gcp-sm://onlyproject", "vault:#field-only",
])
def test_parse_ref_rejects_bad(bad):
    from pfactory_secrets import InvalidSecretRefError
    from pfactory_secrets.refs import parse_ref

    with pytest.raises(InvalidSecretRefError):
        parse_ref(bad)


# ── factory ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("alias,canonical", [
    ("env", "env"), ("environment", "env"),
    ("file", "localfile"), ("sops", "localfile"), ("agenix", "localfile"),
    ("vault", "vault"), ("HCV", "vault"),
    ("azurekv", "azure_keyvault"), ("akv", "azure_keyvault"),
    ("aws-sm", "aws_secrets_manager"), ("asm", "aws_secrets_manager"),
    ("gcp-sm", "gcp_secret_manager"), ("gsm", "gcp_secret_manager"),
])
def test_resolve_canonical_aliases(alias, canonical):
    from pfactory_secrets.factory import resolve_canonical

    assert resolve_canonical(alias) == canonical


def test_factory_instantiates_implemented_backends():
    from pfactory_secrets.backends.env import EnvBackend
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.factory import get_secrets_backend

    assert isinstance(get_secrets_backend("env"), EnvBackend)
    assert isinstance(get_secrets_backend("sops"), LocalFileBackend)


def test_factory_instantiates_cloud_backends():
    """Cloud backends instantiate (SDK imported lazily, only at resolve time)."""
    from pfactory_secrets import SecretsBackend
    from pfactory_secrets.factory import get_secrets_backend

    for name in ("vault", "azurekv", "aws-sm", "gcp-sm"):
        backend = get_secrets_backend(name)
        assert isinstance(backend, SecretsBackend)
        # available() is a safe bool even when the SDK isn't installed.
        assert isinstance(backend.available(), bool)


def test_factory_unknown_backend_raises():
    from pfactory_secrets.factory import get_secrets_backend

    with pytest.raises(ValueError):
        get_secrets_backend("nope")


# ── env backend ─────────────────────────────────────────────────────────────

def test_env_backend_resolves(monkeypatch):
    from pfactory_secrets import EgressClass
    from pfactory_secrets.backends.env import EnvBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.setenv("MY_SECRET", "s3cr3t")
    b = EnvBackend()
    assert b.available() and b.egress_class() is EgressClass.LOCAL
    val = b.resolve(parse_ref("env:MY_SECRET"))
    assert val.value == "s3cr3t" and val.backend == "env"


def test_env_backend_missing_raises(monkeypatch):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.env import EnvBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.delenv("ABSENT_SECRET", raising=False)
    with pytest.raises(SecretNotFoundError):
        EnvBackend().resolve(parse_ref("env:ABSENT_SECRET"))


# ── localfile backend ───────────────────────────────────────────────────────

def test_localfile_plaintext_whole_file(tmp_path):
    from pfactory_secrets import EgressClass
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    p = tmp_path / "token"
    p.write_text("  abc123\n")
    b = LocalFileBackend()
    assert b.egress_class() is EgressClass.LOCAL
    assert b.resolve(parse_ref(f"file:{p}")).value == "abc123"


def test_localfile_field_select(tmp_path):
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    p = tmp_path / "creds.env"
    p.write_text("# comment\napi_token = 'tok-xyz'\nother: nope\n")
    val = LocalFileBackend().resolve(parse_ref(f"file:{p}#api_token"))
    assert val.value == "tok-xyz"


def test_localfile_missing_file(tmp_path):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    with pytest.raises(SecretNotFoundError):
        LocalFileBackend().resolve(parse_ref(f"file:{tmp_path / 'nope'}"))


# NOTE: sops/age/agenix decryption (the encrypted localfile formats) is
# covered in tests/test_secrets_localfile.py (issue #64).


# ── redaction ───────────────────────────────────────────────────────────────

def test_secret_value_repr_redacts():
    from pfactory_secrets import SecretValue

    sv = SecretValue(value="supersecret", backend="env", ref="env:X", source="env:X")
    assert "supersecret" not in repr(sv)
    assert "supersecret" not in str(sv)
    assert sv.value == "supersecret"  # explicit access still works
