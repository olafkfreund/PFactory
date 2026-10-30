"""ID-token claims and signing algorithms are enforced during OIDC callback."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from authlib.jose import JsonWebKey, jwt
from joserfc.errors import InvalidClaimError, MissingClaimError, UnsupportedAlgorithmError

from server.oidc.client import authorize_access_token, get_oauth_client, reset_oauth_client_cache


@pytest.fixture(autouse=True)
def _drop_cached_client():
    # The client is built from this test's env; don't leak it to later tests.
    yield
    reset_oauth_client_cache()


def _exchange(monkeypatch, claims, *, alg="RS256", discovery_issuer="https://idp.example"):
    monkeypatch.setenv("APP_OIDC_ISSUER_URL", "https://idp.example")
    monkeypatch.setenv("APP_OIDC_CLIENT_ID", "client-id")
    monkeypatch.setenv("APP_OIDC_CLIENT_SECRET", "client-secret")
    reset_oauth_client_cache()
    oauth = get_oauth_client()

    rsa_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    keys = [rsa_key.as_dict(is_private=False)]
    signing_key = rsa_key
    algorithms = ["RS256"]
    if alg == "HS256":
        signing_key = JsonWebKey.generate_key("oct", 256, is_private=True)
        keys.append(signing_key.as_dict())
        algorithms.append("HS256")

    oauth.oidc.server_metadata = {
        "_loaded_at": time.time(),
        "issuer": discovery_issuer,
        "jwks": {"keys": keys},
        "id_token_signing_alg_values_supported": algorithms,
    }
    id_token = jwt.encode(
        {"alg": alg, "kid": signing_key.as_dict()["kid"]}, claims, signing_key
    ).decode()

    async def fetch_access_token(**_kwargs):
        return {"access_token": "access-token", "id_token": id_token}

    monkeypatch.setattr(oauth.oidc, "fetch_access_token", fetch_access_token)
    request = SimpleNamespace(
        scope={"method": "GET"},
        query_params={"code": "code", "state": "state"},
        session={},
    )

    async def exchange():
        await oauth.oidc.framework.set_state_data(request.session, "state", {"nonce": "nonce"})
        return await authorize_access_token(oauth.oidc, request)

    return asyncio.run(exchange())


def _claims():
    now = int(time.time())
    return {
        "iss": "https://idp.example",
        "aud": "client-id",
        "exp": now + 300,
        "iat": now,
        "sub": "user-123",
        "nonce": "nonce",
    }


def test_valid_rs256_id_token(monkeypatch):
    token = _exchange(monkeypatch, _claims())
    assert token["userinfo"]["sub"] == "user-123"


def test_issuer_must_match_configured_issuer_not_discovery(monkeypatch):
    claims = _claims()
    claims["iss"] = "https://attacker.example"
    with pytest.raises(InvalidClaimError, match="iss"):
        _exchange(monkeypatch, claims, discovery_issuer=claims["iss"])


def test_exp_is_required(monkeypatch):
    claims = _claims()
    del claims["exp"]
    with pytest.raises(MissingClaimError, match="exp"):
        _exchange(monkeypatch, claims)


def test_audience_must_match_client_id(monkeypatch):
    claims = _claims()
    claims["aud"] = "other-client"
    with pytest.raises(InvalidClaimError, match="aud"):
        _exchange(monkeypatch, claims)


def test_hs256_rejected_even_when_discovery_advertises_it(monkeypatch):
    with pytest.raises(UnsupportedAlgorithmError, match="HS256"):
        _exchange(monkeypatch, _claims(), alg="HS256")
