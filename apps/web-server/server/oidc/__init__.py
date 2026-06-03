"""OIDC SSO layer for PFactory (Epic #26 P3).

Adds OpenID Connect single sign-on alongside the existing local
password + JWT flow. OIDC is a *parallel* way to obtain the internal
JWT — once the callback completes, downstream auth middleware sees an
ordinary access token and doesn't care that it originated from OIDC.

Configured via env vars (mapped from values.yaml in Helm):
  APP_OIDC_ENABLED        — feature flag (default false)
  APP_OIDC_ISSUER_URL     — IdP discovery root, e.g.
                            ``https://keycloak.internal/realms/pfactory``
  APP_OIDC_CLIENT_ID      — relying-party client id
  APP_OIDC_CLIENT_SECRET  — relying-party client secret (confidential)
  APP_OIDC_REDIRECT_URI   — full callback URL exposed by the app, e.g.
                            ``https://pfactory.example.com/api/auth/oidc/callback``
"""

from .client import get_oauth_client, is_oidc_enabled

__all__ = ["get_oauth_client", "is_oidc_enabled"]
