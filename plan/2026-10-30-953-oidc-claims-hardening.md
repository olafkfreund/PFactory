---
status: approved
issue: 953
spec: spec/2026-10-30-953-oidc-claims-hardening.md
coders:
  1: codex
  2: codex
---

# Plan: enforce ID-token iss, exp and asymmetric algs in OIDC login

Decisions (from the spec):
- Enforce per call, not at `register()`: authlib 1.7.2 reads ID-token
  `claims_options` only from `authorize_access_token(claims_options=)` and
  the accepted algs only from the loaded server metadata.
- `iss` = configured `APP_OIDC_ISSUER_URL` (rstrip "/"), `aud` = client_id,
  `exp` essential; algs pinned to RS/ES/PS 256/384/512 over whatever discovery says.
- Codex implements both steps (the user asked for Codex this wave).

## Steps

1. `apps/web-server/server/oidc/client.py`: add `ID_TOKEN_SIGNING_ALGS` and
   `async def authorize_access_token(oidc, request)` per spec Design; export it
   from `server/oidc/__init__.py` next to `get_oauth_client`.
   `apps/web-server/server/routes/oidc_routes.py::oidc_callback`: replace
   `await oauth.oidc.authorize_access_token(request)` with
   `await authorize_access_token(oauth.oidc, request)`, keep the except block.
   → verify: `apps/backend/.venv/bin/pytest apps/web-server/tests/test_oidc_silent.py tests/oidc -q`
   Traps: never `git add -A`; the `apps/backend/.venv` symlink must not be staged.
2. Add `apps/web-server/tests/test_oidc_id_token_hardening.py`: RSA key via
   `authlib.jose.JsonWebKey.generate_key("RSA", 2048, is_private=True)`,
   build a real starlette OAuth app via `get_oauth_client()` with env set,
   stub `load_server_metadata`'s HTTP (set `server_metadata` incl.
   `_loaded_at`, `jwks`, discovery `issuer` and advertised algs),
   `fetch_access_token` and session state (nonce); cases: valid accepted,
   wrong iss with discovery agreeing rejected, no exp rejected, wrong aud
   rejected, HS256 with discovery advertising HS256 rejected.
   → verify: `apps/backend/.venv/bin/pytest apps/web-server/tests/test_oidc_id_token_hardening.py -q`
   and it fails on origin/dev's client (iss and HS256 cases).

## Tests

`apps/backend/.venv/bin/pytest apps/web-server/tests/ tests/oidc -q`, ruff
check + format on touched files. Pre-commit hooks run on commit.

## Rollback

Revert the PR; the callback returns to authlib defaults.

## Deviations (lead review, 2026-10-30)

- `iss` accepts `[issuer, issuer + "/"]`. An exact match would lock out IdPs whose
  `iss` ends in "/" (the Azure AD v1 preset, Auth0).
- The helper is typed for `mypy --strict` (`Request` under `TYPE_CHECKING`,
  `-> dict[str, Any]`), and so is the test.
- The route imports the helper with an absolute `from server.oidc import ...`
  line. The ruff ratchet counts TID252 per name, so adding the name to the
  relative import adds one violation.
- Step 2 adds these cases: `alg=none` rejected even when discovery advertises
  it; configured issuer with a trailing slash; `aud` other with `azp` = client_id
  rejected; and a mutation control (authlib called without the helper accepts
  HS256). The test is shared with AIFactory #1954.
- `routes/oidc_routes.py` also carries ruff autofix churn: `timezone.utc` → `UTC`,
  the unused `Organization`/`OrgMember` imports dropped, and the `userinfo_cache`
  imports merged. PFactory's `.husky/pre-commit` runs `ruff check --fix` + `format`
  with the pinned ruff and re-stages every staged .py file. A revert would be
  rewritten on commit, and `--no-verify` is not allowed, so the churn stays.
