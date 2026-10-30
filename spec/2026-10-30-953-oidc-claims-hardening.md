---
status: approved
issue: 953
intent: intent/2026-10-30-953-oidc-claims-hardening.md
---

# Spec: enforce ID-token iss, exp and asymmetric algs in OIDC login

## Design

In `server/oidc/client.py` add `ID_TOKEN_SIGNING_ALGS` (RS/ES/PS 256-512)
and `async def authorize_access_token(oidc, request)`:

1. `metadata = await oidc.load_server_metadata()` and set
   `metadata["id_token_signing_alg_values_supported"] = ID_TOKEN_SIGNING_ALGS`
   (authlib caches and re-reads this dict, so the pin holds for decode).
2. `return await oidc.authorize_access_token(request, claims_options=...)` with
   `iss` essential and equal to the configured issuer (rstripped like the
   discovery URL), `aud` essential containing the client_id, `exp` essential.

`routes/oidc_routes.py::oidc_callback` calls this helper instead of
`oauth.oidc.authorize_access_token(request)`; its existing except block
turns every rejection into the 400 it already returns.

## Alternatives rejected

- Port AIFactory #324 (register-time kwargs): authlib 1.7.2 ignores them for
  ID-token validation (see intent).
- Subclass authlib's Starlette app: more code for the same two settings.
- Intersect the IdP's advertised algs with ours: same safety, more code; the
  fixed list already covers every asymmetric alg an IdP signs with.

## Risks

- An IdP whose token `iss` differs from `APP_OIDC_ISSUER_URL` (trailing
  slash, internal hostname) now fails login. Live config matches; the
  Keycloak CI acceptance job proves the CI setup.
- Relies on authlib's metadata dict being mutable and cached; the test drives
  the real authlib decode so an authlib change that breaks it fails the test.

## Verification

`apps/web-server/tests/test_oidc_id_token_hardening.py` signs real JWTs and runs them through
the real authlib `authorize_access_token` with only the network stubbed:
valid RS256 accepted; wrong iss (even when discovery reports it), missing
exp, wrong aud and HS256 (even when discovery advertises it) rejected.
Existing `test_oidc_silent.py` and `tests/oidc` still pass; CI green.
