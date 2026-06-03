# Technical Specification

> Spec: Test-Target Authentication + Credential Vault
> Created: 2026-06-02

## Architecture overview

```
Operator (portal)                 .pfactory.yml                  Pipeline (egress lane)
─────────────────                 ─────────────                  ─────────────────────
POST /api/test-credentials  ──►  test_credentials:        ┌────────────────────────────┐
  {name, kind, username,           login:                 │ Evaluator (browser/api lane)│
   secret}                           ref: store:<cred_id> │   network="host" + egress    │
        │ _EncryptedString            as_username: TEST_USERNAME      │
        ▼                             as_secret: TEST_PASSWORD        ▼
  test_target_credentials      targets:                  resolve_test_target_credentials()
  (DB, encrypted, org-scoped)    web: { auth: { ref: login } }   → extra_env (0600, wiped)
                                                                   │
                                                                   ▼
                                                          DockerRunner --network=host
                                                            -e TEST_USERNAME=... (ephemeral)
                                                                   │
                                                                   ▼
                                                          Playwright auth.setup.ts → storageState
                                                            → protected-page test reuses session
```

Two storage backends, one resolution path:
- **Portal-managed** creds live in the DB (`store:<id>` ref), encrypted with the existing `_EncryptedString`.
- **Operator/CI** creds come from the existing broker backends (`env:`, `vault:`, cloud) — no new storage, just a new ref consumer.

Both resolve through `pfactory_secrets` into the same `extra_env` the executor already forwards.

## Components

### 1. Storage — `TestTargetCredential` (web-server)
- New SQLAlchemy model + Alembic migration (see `database-schema.md`).
- Mirror `GitCredential` exactly: org-scoped, `_EncryptedString` columns, `created_by`, never returned after create.
- A credential is `{id, org_id, name, kind, username (nullable), secret (encrypted), extra (encrypted JSON, nullable)}` where `kind ∈ {form, api_token, basic_auth, totp}`.

### 2. Resolution — `pfactory_secrets.resolve_test_target_credentials()`
- New function alongside `resolve_cloud()` in `apps/backend/pfactory_secrets/`.
- Input: the parsed `.pfactory.yml` `test_credentials` block + project/spec dirs.
- For each entry: resolve the ref →
  - `env:NAME` / `vault:path#field` → via existing `CredentialBroker.resolve_ref()`.
  - `store:<cred_id>` → fetch+decrypt from the web-server credential store via an **internal, localhost-only** lookup (see API spec) — the backend agent and web-server share a host.
- Output: `{env: {AS_NAME: value, ...}, files: [...]}` (same shape `resolve_sandbox_credentials` returns), so it merges into `extra_env` and is `wipe()`-d after the run.

### 3. Executor wiring — `sandbox_credentials.py` / `evaluator.py`
- Extend `resolve_sandbox_credentials(project_dir, spec_dir, network)` to also call `resolve_test_target_credentials()` when (a) the lane is egress-enabled (`network != "none"`) AND (b) egress is opted in (`egress_enabled(project_dir)`).
- Merge into `extra_env`; the existing `wipe()`/`close()` cleanup covers the new secrets.
- **Unchanged guarantee:** unit/mutation lanes (`network="none"`) get nothing — hermetic.

### 4. Browser-lane auth — Playwright `storageState`
- Add `apps/backend/agents/evidence/auth.setup.ts.tmpl` — a Playwright **setup project** that logs in once (reads `process.env.TEST_USERNAME/TEST_PASSWORD` + selectors from `.pfactory.yml`) and writes `storageState` to `/scratch/.auth/state.json`.
- Update `playwright.config.tmpl.ts` to add a `setup` project dependency + `use: { storageState }` for the main project, gated on `requires_auth`.
- Rewrite `frameworks/playwright/templates/login-flow.spec.ts.tmpl` to consume injected env vars instead of hardcoded `test-user@example.com`.

### 5. Planner / Gen-Functional `requires_auth`
- Planner: when a target has `auth.ref` or an AC mentions login, tag the subtask `requires_auth: true` (a new optional field on the subtask schema, default `false`).
- Gen-Functional: when `requires_auth`, generate against the `storageState`-backed config and ensure the protected-page test does **not** re-login.
- Evaluator: for `requires_auth` browser subtasks, ensure the egress lane + creds are resolved before running.

## Security requirements (must-haves)

- **At rest:** `_EncryptedString` (existing KMS/Vault/Azure/GCP backends via Epic #26 P2). No plaintext column.
- **In flight:** creds only as ephemeral container env (`-e`) or 0600 scratch files, read-only mounted, **wiped after every run** (`sandbox_creds.wipe()`).
- **Egress-gated:** never injected for `network="none"` lanes; only when `egress_enabled(project_dir)` is true.
- **No leakage:** add a redaction pass so resolved secret *values* are scrubbed from logs, junit, coverage, evidence (HAR!), verdicts, and triage reports. HAR capture (`agents/evidence/http_recorder.py`) is the highest-risk sink — must redact `Authorization`/cookie headers + form bodies matching a credential value.
- **Never returned:** the portal API returns credential *metadata* only (name/kind/created), never the secret — exactly like `GitCredential`.

## Network / egress posture

No change to defaults. This rides the **already-existing** egress path: `network="host"` for browser/api lanes (`evaluator.py:751,1228`) + `egress_enabled` opt-in. A `.pfactory.yml` using `test_credentials` without egress enabled fails closed with a clear error.

## Backward compatibility

- New `.pfactory.yml` `test_credentials` + subtask `requires_auth` are **optional**; existing configs/plans unaffected.
- The login-flow template change is additive (new vars; the fake-cred path remains for `requires_auth: false` smoke tests).
- No change to unit/mutation/hermetic behaviour.
