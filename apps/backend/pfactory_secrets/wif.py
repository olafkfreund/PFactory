"""Workload-identity federation — short-lived scoped credentials (#74).

Mints **ephemeral** cloud credentials from an OIDC token instead of a
long-lived secret:

- **AWS** — STS ``AssumeRoleWithWebIdentity`` (implemented).
- **GCP** — Workload Identity Federation (routed; fast-follow).
- **Azure** — federated tokens (routed; fast-follow).

The minted credentials carry an expiry; the broker caches them per provider and
re-mints once they near expiry (see ``CredentialBroker._resolve_wif``). The AWS
path lazy-imports ``boto3`` through :func:`_sts_client`, which tests replace with
a stub — no real STS call and no hard boto3 dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Refresh creds this many seconds before their hard expiry.
_EXPIRY_SKEW_SECONDS = 60.0


class WifError(Exception):
    """A workload-identity-federation mint failed."""


@dataclass(frozen=True)
class WifCredentials:
    """Short-lived federated credentials + their expiry (epoch seconds, UTC)."""

    provider: str
    env: dict[str, str]
    expires_at: float

    def expired(self, now: float, skew: float = _EXPIRY_SKEW_SECONDS) -> bool:
        """True once ``now`` is within ``skew`` seconds of hard expiry."""
        return now >= (self.expires_at - skew)


def _read_oidc_token(config) -> str:
    """Read the OIDC token from ``config.token`` or ``config.token_file``.

    ``OperatorWifEntry.token`` is a ``SecretStr`` since Factory#377, so the
    plaintext has to be unwrapped here -- the one place it is used. The
    ``hasattr`` check keeps the duck-typed contract this function was written
    with (``getattr`` on an arbitrary config object), and matters because that
    dynamic access is exactly what a type checker cannot see through: without
    it, a plain ``SecretStr`` would reach ``WebIdentityToken`` below and STS
    would be handed the string ``**********`` with nothing flagging it.
    """
    token = getattr(config, "token", None)
    if token:
        return token.get_secret_value() if hasattr(token, "get_secret_value") else str(token)
    token_file = getattr(config, "token_file", None)
    if token_file:
        p = Path(token_file).expanduser()
        if not p.exists():
            raise WifError(f"OIDC token_file not found: {p}")
        return p.read_text(encoding="utf-8").strip()
    raise WifError("WIF entry needs either 'token' or 'token_file'")


def _sts_client():
    """Return a boto3 STS client. Lazy import; tests stub this whole function."""
    import boto3  # noqa: PLC0415 - lazy so boto3 isn't a hard dependency

    return boto3.client("sts")


def _mint_aws(config, *, now: float) -> WifCredentials:
    """AWS STS ``AssumeRoleWithWebIdentity`` → short-lived keys."""
    role_arn = getattr(config, "role_arn", None)
    if not role_arn:
        raise WifError("AWS WIF entry needs 'role_arn'")
    token = _read_oidc_token(config)
    client = _sts_client()
    resp = client.assume_role_with_web_identity(
        RoleArn=role_arn,
        RoleSessionName=getattr(config, "session_name", "pfactory"),
        WebIdentityToken=token,
        DurationSeconds=int(getattr(config, "duration_seconds", 3600)),
    )
    creds = resp["Credentials"]
    expiration = creds["Expiration"]
    # boto3 returns a tz-aware datetime; fall back to duration if it's odd.
    try:
        expires_at = expiration.timestamp()
    except AttributeError:
        expires_at = now + int(getattr(config, "duration_seconds", 3600))
    env = {
        "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
        "AWS_SESSION_TOKEN": creds["SessionToken"],
    }
    return WifCredentials(provider="aws", env=env, expires_at=expires_at)


def mint_wif(provider: str, config, *, now: float) -> WifCredentials | None:
    """Mint short-lived federated creds for ``provider``.

    Returns ``None`` when no WIF config applies. Raises :class:`WifError` on a
    configured-but-failed mint, and ``NotImplementedError`` for providers whose
    federation is routed but not yet implemented (GCP / Azure).
    """
    if config is None:
        return None
    if provider == "aws":
        return _mint_aws(config, now=now)
    if provider in ("gcp", "azure"):
        raise NotImplementedError(
            f"{provider} workload-identity federation is not implemented yet (#74 fast-follow)"
        )
    return None


__all__ = ["WifCredentials", "WifError", "mint_wif"]
