"""Pytest fixtures for P3 OIDC SSO acceptance tests.

Tests are marked ``@pytest.mark.oidc``. They run against a Keycloak
Docker container in CI (the ``oidc-acceptance`` job in
``.github/workflows/ci.yml``); locally they skip unless
``OIDC_ISSUER_URL`` + ``OIDC_CLIENT_ID`` are set in the env.

Until P3.1 lands the actual OIDC client + Keycloak service container,
every test in ``test_p3_oidc.py`` is decorated with
``@pytest.mark.skip(reason="P3.x implementation pending: ...")`` so the
suite collects cleanly and CI is green from day one.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def oidc_issuer_url() -> str:
    """The IdP's discovery root, e.g. ``http://localhost:8080/realms/pfactory``.

    Tests that need a real IdP must depend on this fixture so they skip
    cleanly when the env isn't wired (typical local runs).
    """
    url = os.environ.get("OIDC_ISSUER_URL")
    if not url:
        pytest.skip(
            "OIDC_ISSUER_URL not set; OIDC acceptance tests require a "
            "running Keycloak (or other IdP) — CI's oidc-acceptance job "
            "boots one as a service container"
        )
    return url


@pytest.fixture
def oidc_client_id() -> str:
    """The relying-party client_id registered in the IdP."""
    cid = os.environ.get("OIDC_CLIENT_ID")
    if not cid:
        pytest.skip("OIDC_CLIENT_ID not set")
    return cid


@pytest.fixture
def oidc_client_secret() -> str:
    """The relying-party client_secret. Required for confidential clients;
    public-client tests (PKCE-only, no secret) won't depend on this fixture."""
    secret = os.environ.get("OIDC_CLIENT_SECRET")
    if not secret:
        pytest.skip("OIDC_CLIENT_SECRET not set")
    return secret


@pytest.fixture(autouse=True)
def clean_oidc_env(monkeypatch):
    """Strip any APP_OIDC_* leftovers between tests so config doesn't leak.

    Mirrors the ``clean_kms_env`` autouse fixture in tests/secrets/.
    The test-side ``OIDC_*`` env (issuer URL etc.) is left alone — those
    point at the CI service container.
    """
    for key in list(os.environ):
        if key.startswith("APP_OIDC_"):
            monkeypatch.delenv(key, raising=False)
    yield
