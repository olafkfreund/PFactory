"""Workload-identity federation — short-lived scoped creds (#74).

Covers the AWS STS mint (mocked), TTL/expiry, the routed GCP/Azure stubs, and
the broker's resolve_cloud WIF head incl. the refresh-on-expiry path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pfactory_secrets import wif
from pfactory_secrets.operator_config import OperatorWifEntry


class _FakeSTS:
    """Stub STS client with a call counter + canned Expiration."""

    def __init__(self, expiration: datetime) -> None:
        self.calls = 0
        self._exp = expiration

    def assume_role_with_web_identity(self, **kwargs) -> dict:
        self.calls += 1
        assert kwargs["RoleArn"] and kwargs["WebIdentityToken"]
        return {
            "Credentials": {
                "AccessKeyId": f"AKIA{self.calls}",
                "SecretAccessKey": "secret",
                "SessionToken": "session-token",
                "Expiration": self._exp,
            }
        }


def _entry(**over) -> OperatorWifEntry:
    base = {"role_arn": "arn:aws:iam::1:role/tf", "token": "oidc-jwt"}
    base.update(over)
    return OperatorWifEntry(**base)


def test_mint_aws_returns_short_lived_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    fake = _FakeSTS(exp)
    monkeypatch.setattr(wif, "_sts_client", lambda: fake)
    creds = wif.mint_wif("aws", _entry(), now=0.0)
    assert creds.provider == "aws"
    assert creds.env["AWS_ACCESS_KEY_ID"] == "AKIA1"
    assert creds.env["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert creds.env["AWS_SESSION_TOKEN"] == "session-token"
    assert creds.expires_at == pytest.approx(exp.timestamp())


def test_expired_respects_skew() -> None:
    creds = wif.WifCredentials("aws", {}, expires_at=1000.0)
    assert creds.expired(now=1000.0) is True           # past hard expiry
    assert creds.expired(now=950.0, skew=60.0) is True  # within skew window
    assert creds.expired(now=800.0, skew=60.0) is False


def test_token_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tok = tmp_path / "token.jwt"
    tok.write_text("file-jwt\n")
    fake = _FakeSTS(datetime.now(timezone.utc) + timedelta(hours=1))
    captured = {}
    monkeypatch.setattr(wif, "_sts_client", lambda: fake)

    def _spy(**kw):
        captured.update(kw)
        return _FakeSTS.assume_role_with_web_identity(fake, **kw)

    fake.assume_role_with_web_identity = _spy  # type: ignore[method-assign]
    wif.mint_wif("aws", _entry(token=None, token_file=str(tok)), now=0.0)
    assert captured["WebIdentityToken"] == "file-jwt"


def test_missing_token_raises() -> None:
    with pytest.raises(wif.WifError):
        wif.mint_wif("aws", _entry(token=None, token_file=None), now=0.0)


def test_gcp_azure_routed_not_implemented() -> None:
    for provider in ("gcp", "azure"):
        with pytest.raises(NotImplementedError):
            wif.mint_wif(provider, _entry(), now=0.0)


# ── broker integration ──────────────────────────────────────────────────────


def _wif_config_file(tmp_path: Path) -> Path:
    import json

    p = tmp_path / "credentials.json"
    p.write_text(
        json.dumps({"wif": {"aws": {"role_arn": "arn:aws:iam::1:role/tf", "token": "oidc-jwt"}}})
    )
    p.chmod(0o600)
    return p


def test_broker_resolve_cloud_uses_wif(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pfactory_secrets import broker

    monkeypatch.setattr(broker, "CREDENTIALS_CONFIG_PATH", _wif_config_file(tmp_path))
    broker.reset_config_cache()
    fake = _FakeSTS(datetime.now(timezone.utc) + timedelta(hours=1))
    monkeypatch.setattr(wif, "_sts_client", lambda: fake)
    try:
        b = broker.CredentialBroker(tmp_path, tmp_path, egress_allowed=True)
        status = b.resolve_cloud("aws")
        assert status.available is True
        assert status.source == "wif:aws"
        assert status.env_vars["AWS_ACCESS_KEY_ID"] == "AKIA1"
        # Second resolve reuses the cached (unexpired) creds — no new STS call.
        b.resolve_cloud("aws")
        assert fake.calls == 1
    finally:
        broker.reset_config_cache()


def test_broker_refreshes_expired_wif(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pfactory_secrets import broker

    monkeypatch.setattr(broker, "CREDENTIALS_CONFIG_PATH", _wif_config_file(tmp_path))
    broker.reset_config_cache()
    # Expiration already in the past → every resolve must re-mint (refresh).
    fake = _FakeSTS(datetime.now(timezone.utc) - timedelta(minutes=5))
    monkeypatch.setattr(wif, "_sts_client", lambda: fake)
    try:
        b = broker.CredentialBroker(tmp_path, tmp_path, egress_allowed=True)
        b.resolve_cloud("aws")
        b.resolve_cloud("aws")
        assert fake.calls == 2  # re-minted because cached creds had expired
    finally:
        broker.reset_config_cache()
