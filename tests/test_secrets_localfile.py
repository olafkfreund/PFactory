#!/usr/bin/env python3
"""
Encrypted local-file backend tests (epic #62, issue #64): sops / age / agenix
decryption. The decryption shell-out is exercised through the mockable
``_run_decrypt`` seam so these run without real binaries or keys.
"""

import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture
def fake_decrypt(monkeypatch):
    """Patch the decryption seam to return canned plaintext + record the cmd."""
    import pfactory_secrets.backends.localfile as lf

    calls = {}

    def _fake(cmd, path):
        calls["cmd"] = cmd
        calls["path"] = path
        return calls["output"]

    monkeypatch.setattr(lf, "_run_decrypt", _fake)
    return calls


def test_sops_decrypt_whole_and_field(monkeypatch, tmp_path, fake_decrypt):
    import pfactory_secrets.backends.localfile as lf
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    # Pretend sops is installed.
    monkeypatch.setattr(lf.shutil, "which", lambda b: "/usr/bin/sops" if b == "sops" else None)
    p = tmp_path / "secrets.enc.yaml"
    p.write_text("placeholder\n")

    fake_decrypt["output"] = "api_token: tok-xyz\nother: nope\n"
    b = LocalFileBackend()

    # whole-file
    whole = b.resolve(parse_ref(f"sops:{p}"))
    assert "api_token: tok-xyz" in whole.value
    assert fake_decrypt["cmd"][:2] == ["/usr/bin/sops", "-d"]

    # field-select
    val = b.resolve(parse_ref(f"sops:{p}#api_token"))
    assert val.value == "tok-xyz" and val.backend == "localfile"


def test_sops_missing_binary_raises(monkeypatch, tmp_path):
    import pfactory_secrets.backends.localfile as lf
    from pfactory_secrets import BackendUnavailableError
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.setattr(lf.shutil, "which", lambda b: None)  # no sops
    p = tmp_path / "s.enc.yaml"
    p.write_text("x\n")
    with pytest.raises(BackendUnavailableError, match="sops CLI not found"):
        LocalFileBackend().resolve(parse_ref(f"sops:{p}"))


def test_age_decrypt_with_identity(monkeypatch, tmp_path, fake_decrypt):
    import pfactory_secrets.backends.localfile as lf
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.setattr(lf.shutil, "which", lambda b: "/usr/bin/age" if b == "age" else None)
    identity = tmp_path / "key.txt"
    identity.write_text("AGE-SECRET-KEY-1FAKE\n")
    monkeypatch.setenv("PFACTORY_AGE_IDENTITY", str(identity))

    enc = tmp_path / "token.age"
    enc.write_text("encrypted\n")
    fake_decrypt["output"] = "super-token\n"

    val = LocalFileBackend().resolve(parse_ref(f"age:{enc}"))
    assert val.value == "super-token"
    assert "-i" in fake_decrypt["cmd"] and str(identity) in fake_decrypt["cmd"]


def test_agenix_uses_age_path(monkeypatch, tmp_path, fake_decrypt):
    import pfactory_secrets.backends.localfile as lf
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.setattr(lf.shutil, "which", lambda b: "/usr/bin/rage" if b == "rage" else None)
    identity = tmp_path / "id.txt"
    identity.write_text("AGE-SECRET-KEY-1FAKE\n")
    monkeypatch.setenv("PFACTORY_AGE_IDENTITY", str(identity))

    enc = tmp_path / "db-password.age"
    enc.write_text("enc\n")
    fake_decrypt["output"] = "p@ss"

    val = LocalFileBackend().resolve(parse_ref(f"agenix:{enc}"))
    assert val.value == "p@ss"
    assert fake_decrypt["cmd"][0] == "/usr/bin/rage"  # falls back to rage


def test_age_missing_identity_raises(monkeypatch, tmp_path):
    import pfactory_secrets.backends.localfile as lf
    from pfactory_secrets import BackendUnavailableError
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.setattr(lf.shutil, "which", lambda b: "/usr/bin/age" if b == "age" else None)
    for env in lf._AGE_IDENTITY_ENV:
        monkeypatch.delenv(env, raising=False)
    # Point defaults at a nonexistent dir so discovery fails deterministically.
    monkeypatch.setattr(lf, "_AGE_IDENTITY_DEFAULTS", (str(tmp_path / "nope"),))

    enc = tmp_path / "x.age"
    enc.write_text("enc\n")
    with pytest.raises(BackendUnavailableError, match="No age identity"):
        LocalFileBackend().resolve(parse_ref(f"age:{enc}"))


def test_decrypt_nonzero_exit_raises(monkeypatch, tmp_path):
    import pfactory_secrets.backends.localfile as lf
    from pfactory_secrets import SecretsError
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    monkeypatch.setattr(lf.shutil, "which", lambda b: "/usr/bin/sops" if b == "sops" else None)

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "sops: no key"

    monkeypatch.setattr(lf.subprocess, "run", lambda *a, **k: _Proc())
    p = tmp_path / "s.enc.yaml"
    p.write_text("x\n")
    with pytest.raises(SecretsError, match="failed"):
        LocalFileBackend().resolve(parse_ref(f"sops:{p}"))


def test_missing_file_still_raises_before_decrypt(tmp_path):
    from pfactory_secrets import SecretNotFoundError
    from pfactory_secrets.backends.localfile import LocalFileBackend
    from pfactory_secrets.refs import parse_ref

    with pytest.raises(SecretNotFoundError):
        LocalFileBackend().resolve(parse_ref(f"sops:{tmp_path / 'absent.enc.yaml'}"))
