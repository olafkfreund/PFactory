"""Authlib OAuth/OIDC client setup.

Single process-global ``OAuth`` registry. The Keycloak/Okta/Azure-AD
preset is selected by name at request time; for v1 we register one
provider keyed ``oidc`` and configure it from env. P3.6 will add named
presets so an operator can switch IdP without restarting (selecting
between pre-registered ``keycloak``/``okta``/``azure_ad`` configs).
"""

from __future__ import annotations

import os
from functools import lru_cache


def is_oidc_enabled() -> bool:
    """True iff the deployment has APP_OIDC_ENABLED=true and the
    minimum env vars are wired. Routes guard themselves with this so
    they can return 404 (not 500) on un-configured installs."""
    if os.environ.get("APP_OIDC_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return False
    return all(
        os.environ.get(k)
        for k in ("APP_OIDC_ISSUER_URL", "APP_OIDC_CLIENT_ID", "APP_OIDC_CLIENT_SECRET")
    )


@lru_cache(maxsize=1)
def get_oauth_client():
    """Return the authlib ``OAuth`` registry singleton with ``oidc``
    provider registered.

    Lazy + cached: authlib's import path is heavyweight, so we don't
    pay it for the local password-login flow.
    """
    # Lazy import so apps/backend test paths that never touch OIDC
    # don't pay the authlib + requests + cryptography import cost.
    from authlib.integrations.starlette_client import OAuth

    from .presets import current_preset

    preset = current_preset()
    issuer = os.environ["APP_OIDC_ISSUER_URL"].rstrip("/")
    client_id = os.environ["APP_OIDC_CLIENT_ID"]
    client_secret = os.environ["APP_OIDC_CLIENT_SECRET"]

    # Operator can override the IdP-specific default with an explicit
    # APP_OIDC_SCOPE (e.g. add custom claims like 'phone' or
    # 'offline_access' for back-channel logout).
    scope = os.environ.get("APP_OIDC_SCOPE") or preset.default_scope

    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_kwargs={
            # PKCE is mandatory per the P3 acceptance criteria. authlib
            # auto-generates code_verifier + code_challenge when this is
            # set.
            "scope": scope,
            "code_challenge_method": "S256",
        },
    )
    return oauth


def reset_oauth_client_cache() -> None:
    """Test hook: drop the lru_cache so the next call re-reads env."""
    get_oauth_client.cache_clear()
