"""IdP-specific presets (Epic #26 P3.6).

Each cloud IdP has slightly different claim conventions:
  Keycloak  — ``groups`` claim is a list of group names
              (e.g., ["pfactory-admin", "pfactory-member"]).
  Okta      — ``groups`` claim is a list; group names tend to be
              namespaced ("PFactory/Admin"). Recommended scope is
              ``openid profile email groups`` (the ``groups`` scope
              must be added to the app's OIDC settings in the Okta
              admin console).
  Azure AD  — ``groups`` claim contains *object IDs*, not group
              names. Operators typically map AAD group OIDs to roles
              via APP_OIDC_GROUP_TO_ROLE.

This module exposes a small registry so operators can opt into a
preset via ``APP_OIDC_PROVIDER`` (defaults to "keycloak"). The
preset controls:
  - default ``scope`` (Okta needs explicit ``groups``)
  - default claim-mapping behavior for non-standard claims
  - documentation pointers in error messages

For the v1.0 enterprise pilot, only the Keycloak path has automated
acceptance tests (LocalStack/Vault-style faithful emulator). Okta +
Azure AD presets are exercised at deploy time against real tenants;
the test stubs in test_p3_oidc.py demonstrate the test shape but
skip without real credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class OidcPreset:
    """Per-IdP claim conventions + default scopes."""

    name: str
    default_scope: str
    groups_claim: str
    # Some IdPs return groups as a comma-separated string; others a
    # JSON array. Authlib normalizes to whatever Python sees in the
    # ID token — operators can override per-deploy.
    groups_as_list: bool
    docs_url: str


PRESETS: dict[str, OidcPreset] = {
    "keycloak": OidcPreset(
        name="keycloak",
        default_scope="openid profile email",
        groups_claim="groups",
        groups_as_list=True,
        docs_url="https://www.keycloak.org/docs/latest/server_admin/",
    ),
    "okta": OidcPreset(
        name="okta",
        # Okta needs `groups` scope explicitly added in the admin
        # console AND the resulting claim included in the token.
        default_scope="openid profile email groups",
        groups_claim="groups",
        groups_as_list=True,
        docs_url=(
            "https://developer.okta.com/docs/guides/customize-tokens-returned-from-okta/"
        ),
    ),
    "azure_ad": OidcPreset(
        name="azure_ad",
        # Azure AD uses GroupMembershipClaims setting in the app
        # registration manifest to emit `groups` (object IDs).
        default_scope="openid profile email",
        groups_claim="groups",
        groups_as_list=True,
        docs_url=(
            "https://learn.microsoft.com/en-us/entra/identity-platform/"
            "id-token-claims-reference"
        ),
    ),
}


def current_preset() -> OidcPreset:
    """Return the active preset based on ``APP_OIDC_PROVIDER`` env."""
    import os
    name = (os.environ.get("APP_OIDC_PROVIDER") or "keycloak").lower()
    return PRESETS.get(name, PRESETS["keycloak"])
