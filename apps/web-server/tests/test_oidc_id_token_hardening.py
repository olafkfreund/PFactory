"""ID-token validation through the configured Authlib Starlette client."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from authlib.jose import JsonWebKey, jwt
from joserfc.errors import (
    InvalidClaimError,
    MissingClaimError,
    UnsupportedAlgorithmError,
)

from server.oidc import client as oidc_client
from server.oidc.client import (
    authorize_access_token,
    get_oauth_client,
    reset_oauth_client_cache,
)


@pytest.fixture(autouse=True)
def _drop_cached_client() -> Iterator[None]:
    yield
    reset_oauth_client_cache()


def _authorize(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any] | None = None,
    *,
    issuer: str = "https://issuer.example/realms/test",
    alg: str = "RS256",
    discovery_issuer: str | None = None,
) -> dict[str, Any]:
    client_id = "test-client"
    nonce = "test-nonce"
    now = int(time.time())
    monkeypatch.setenv("APP_OIDC_ISSUER_URL", issuer)
    monkeypatch.setenv("APP_OIDC_CLIENT_ID", client_id)
    monkeypatch.setenv("APP_OIDC_CLIENT_SECRET", "test-secret")
    reset_oauth_client_cache()
    oidc = get_oauth_client().oidc

    rsa_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    keys = [rsa_key.as_dict(is_private=False)]
    signing_key = rsa_key
    if alg == "HS256":
        signing_key = JsonWebKey.generate_key("oct", 256, is_private=True)
        keys.append(signing_key.as_dict())
    oidc.server_metadata = {
        "_loaded_at": time.time(),
        "issuer": discovery_issuer or issuer,
        "jwks": {"keys": keys},
        "id_token_signing_alg_values_supported": ["RS256", alg],
    }
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": client_id,
        "exp": now + 3600,
        "iat": now,
        "sub": "user-123",
        "nonce": nonce,
    }
    if claims:
        payload.update(claims)
    if claims is not None and "exp" in claims and claims["exp"] is None:
        payload.pop("exp")

    if alg == "none":

        def encode_part(value: dict[str, Any]) -> bytes:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")

        signed = encode_part({"alg": "none"}) + b"." + encode_part(payload) + b"."
    else:
        signed = jwt.encode({"alg": alg, "kid": signing_key.as_dict()["kid"]}, payload, signing_key)

    async def fetch_access_token(**_kwargs: Any) -> dict[str, Any]:
        return {"access_token": "access-token", "id_token": signed}

    monkeypatch.setattr(oidc, "fetch_access_token", fetch_access_token)
    # Stands in for a starlette Request; authlib only reads these attributes.
    request: Any = SimpleNamespace(
        scope={"method": "GET"},
        query_params={"code": "test-code", "state": "test-state"},
        session={},
    )

    async def run() -> dict[str, Any]:
        await oidc.framework.set_state_data(request.session, "test-state", {"nonce": nonce})
        result: dict[str, Any] = await authorize_access_token(oidc, request)
        return result

    return asyncio.run(run())


def test_valid_rs256_id_token_without_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _authorize(monkeypatch)
    assert token["userinfo"]["sub"] == "user-123"


def test_valid_rs256_id_token_with_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _authorize(monkeypatch, issuer="https://issuer.example/realms/test/")
    assert token["userinfo"]["sub"] == "user-123"


def test_discovery_cannot_override_configured_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_issuer = "https://other.example/realms/test"
    with pytest.raises(InvalidClaimError):
        _authorize(monkeypatch, {"iss": wrong_issuer}, discovery_issuer=wrong_issuer)


def test_missing_exp_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MissingClaimError):
        _authorize(monkeypatch, {"exp": None})


def test_wrong_audience_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InvalidClaimError):
        _authorize(monkeypatch, {"aud": "other-client"})


def test_azp_cannot_override_wrong_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InvalidClaimError):
        _authorize(monkeypatch, {"aud": "other-client", "azp": "test-client"})


def test_discovery_cannot_enable_hs256(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnsupportedAlgorithmError):
        _authorize(monkeypatch, alg="HS256")


def test_discovery_cannot_enable_none(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnsupportedAlgorithmError):
        _authorize(monkeypatch, alg="none")


def test_hs256_passes_once_the_pin_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mutation control: the HS256 rejection above comes from our pin, not authlib.
    monkeypatch.setattr(oidc_client, "ID_TOKEN_SIGNING_ALGS", ["RS256", "HS256"])
    token = _authorize(monkeypatch, alg="HS256")
    assert token["userinfo"]["sub"] == "user-123"
