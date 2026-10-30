---
status: approved
issue: 953
author: olafkfreund
---

# Intent: enforce ID-token iss, exp and asymmetric algs in OIDC login

Refs olafkfreund/Factory#312.

## Problem

`apps/web-server/server/oidc/client.py` registers the authlib client with
no ID-token claim rules and no algorithm pin. With authlib 1.7.2 that means:

- `iss` is checked only against the `issuer` the IdP's discovery document
  reports, not against the configured `APP_OIDC_ISSUER_URL`.
- the accepted signing algorithms are whatever the discovery document
  advertises (`id_token_signing_alg_values_supported`), so an IdP or a
  tampered discovery response can widen them to HS* (HMAC confusion).

AIFactory #324 tried to fix this by passing `claims_options` and the alg list
to `oauth.register()`. Reading authlib 1.7.2: `register()` stores unknown
kwargs as server metadata that `parse_id_token` never reads for claims, and
`client_kwargs` go to the HTTP session; `parse_id_token` takes
`claims_options` only from `authorize_access_token(..., claims_options=)`
and the algs only from the loaded server metadata. A straight port would be
cosmetic, so this task does not copy it.

## Proposed outcome

The OIDC callback rejects an ID token whose `iss` is not the configured
issuer, whose `aud` lacks our client_id, that has no `exp`, or that is signed
with anything but an RS/ES/PS algorithm, whatever discovery says. Valid
Keycloak logins keep working.

## Affected users and systems

PFactory web-server OIDC login (`/api/auth/oidc/callback`). Deployed issuer is the
Keycloak `factory` realm; its `KC_HOSTNAME` equals `APP_OIDC_ISSUER_URL`
in factory-gitops, so the iss pin matches live tokens.

## Constraints

- Must not lock out OIDC logins; existing OIDC tests must pass.
- No config, gitops or deploy change.

## Open questions

None. (AIFactory's #324 has the same no-op; reported to the lead, not fixed here.)
