# API Specification

> Spec: Test-Target Authentication + Credential Vault
> Created: 2026-06-02

## 1. Portal REST — `/api/test-credentials`

Mirrors `apps/web-server/server/routes/git_credentials.py`. Org-scoped; auth via the existing portal auth. **The secret is never returned after create.**

### `POST /api/test-credentials`
Create a credential (encrypted at rest).
```jsonc
// request
{ "name": "staging-login", "kind": "form",
  "username": "qa@acme.test", "secret": "•••••",
  "extra": { "otp_period": 30 } }            // optional, encrypted
// response 201 — metadata only, NO secret
{ "id": "tc_a1b2", "name": "staging-login", "kind": "form",
  "username": "qa@acme.test", "created_at": "2026-06-02T...", "last_used_at": null }
```

### `GET /api/test-credentials`
List org credentials — **metadata only** (`id, name, kind, username, created_at, last_used_at`). Never the secret.

### `DELETE /api/test-credentials/{id}`
Delete (creator or org admin), matching `git_credentials` authz.

> Error contract follows the repo convention: success returns the raw object; errors return `{success:false, error}` (see web-server patterns in CLAUDE.md).

## 2. Internal resolver lookup (backend ↔ web-server, localhost-only)

The backend agent resolves a `store:<id>` ref by fetching+decrypting from the web-server on the shared host. Two acceptable implementations (decide in tasks):
- **(A) Direct DB read** from the backend using the same `_EncryptedString` decryption (no network) — simplest, no new endpoint.
- **(B) A loopback-only internal endpoint** `GET /internal/test-credentials/{id}/resolve` bound to `127.0.0.1`, authenticated by a per-host shared token.

Recommendation (revised during task 2): **(B) web-server materialises `store:`**.
The backend agent runs in a **separate venv without the DB driver**
(sqlalchemy/the encrypted-column stack live only in the web-server venv), so
**(A) direct decryption from the backend is infeasible**. Instead the
web-server resolves a `store:<id>` into the credential value at hand-off and
passes it to the backend as an `env:` ref (or pre-injected env). The backend
`resolve_test_target_credentials` (task 2) only ever sees broker schemes and
skips any stray `store:` ref. The loopback endpoint (B) remains an option if a
cross-process resolve is ever needed.

## 3. `.pfactory.yml` schema additions

Extend `apps/backend/pfactory_yml/schema.py`.

```yaml
version: 1

# NEW: name → where the secret comes from → how it is exposed to the test
test_credentials:
  login:
    ref: store:tc_a1b2          # store:<id> | env:NAME | vault:secret/path#field
    kind: form                  # form | api_token | basic_auth | totp
    as_username: TEST_USERNAME  # env var injected into the run (plaintext username)
    as_secret: TEST_PASSWORD    # env var injected (the decrypted secret)

targets:
  - name: web
    type: http
    base_url: https://staging.acme.test
    auth:
      ref: login                # NEW: reference a test_credentials entry
      login_url: /login         # for kind=form
      username_selector: "#email"
      password_selector: "#password"
      submit_selector: "button[type=submit]"
      success_url_pattern: "**/dashboard"
    health_check: { path: /healthz, expect_status: 200 }
```

Validation rules:
- A `targets[].auth.ref` must name an entry in `test_credentials`.
- `test_credentials[].ref` must be a parseable broker ref (`store:` / `env:` / `vault:`).
- Using `test_credentials` requires egress opted in for the relevant lane; otherwise fail closed with a clear message.

## 4. Subtask schema addition (Planner → Gen-Functional)

Add an optional `requires_auth: bool = false` to the subtask schema (`test_plan/`). When true:
- the browser/api lane runs egress-enabled with creds resolved,
- the generated Playwright test uses the `storageState`-backed config,
- the Evaluator asserts the auth setup project ran before the test.

## 5. Injected runtime env (inside the sandbox)

For an egress browser/api lane with `requires_auth`:
```
TEST_USERNAME=<plaintext username>     # ephemeral, -e flag
TEST_PASSWORD=<decrypted secret>       # ephemeral, -e flag, redacted from all logs/artifacts
PFACTORY_TARGET_URL=<base_url>         # already injected today
```
All wiped after the run via the existing `sandbox_creds.wipe()`.
