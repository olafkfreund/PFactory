# Tests Specification

> Spec: Test-Target Authentication + Credential Vault
> Created: 2026-06-02

## Unit tests

### Storage (`test_test_target_credential_model.py`)
- create persists with encrypted `secret`/`extra` (raw column ≠ plaintext)
- `UNIQUE(org_id, name)` rejects a duplicate name in the same org
- model never exposes the secret via its serialiser

### Resolver (`test_resolve_test_target_credentials.py`) — add to the `critical` lane
- `env:NAME` ref resolves to the env value
- `vault:path#field` ref delegates to `CredentialBroker.resolve_ref`
- `store:<id>` ref decrypts from the credential store
- output shape is `{env, files}`; `as_username`/`as_secret` mapped to the configured env names
- hermetic lane (`network="none"`) → returns nothing
- egress lane with `egress_enabled=False` → returns nothing (fail-closed)
- `wipe()` removes materialised files

### `.pfactory.yml` schema (`test_pfactory_yml_test_credentials.py`)
- valid `test_credentials` + `targets[].auth.ref` parse
- `auth.ref` naming an unknown credential → validation error
- unparseable `ref` → validation error

### Redaction (`test_secret_redaction.py`) — **critical**
- a resolved secret value is scrubbed from: stdout/stderr logs, junit.xml, coverage, the HAR (`agents/evidence/http_recorder.py` — `Authorization`/cookie/form-body), verdicts.json, triage_report.{md,json}

## Integration tests

### Executor wiring (`test_executor_injects_test_creds.py`)
- browser lane + `requires_auth` + egress on → `extra_env` contains `TEST_USERNAME`/`TEST_PASSWORD`; wiped after
- unit lane → no creds in `extra_env`

### Playwright auth (`test_playwright_storage_state.py` / runner smoke)
- `auth.setup.ts` logs in once and writes `/scratch/.auth/state.json`
- the protected-page test reuses `storageState` and does **not** re-login (no second login request in the HAR)

## Portal API tests (web-server)
- `POST /api/test-credentials` returns metadata only (no secret in response body)
- `GET` lists metadata only
- `DELETE` enforces creator/admin authz
- secret never appears in any response after creation (parametrised over all routes)

## Frontend
- Settings panel renders, creates, lists (metadata), and deletes a credential
- `npm run typecheck` passes

## Migration
- `postgres (P1 acceptance)` job: the new Alembic revision applies cleanly on PG 15 + 16

## Manual / e2e (documented, not gated)
- Against a throwaway app with a login form: store a credential, mark a browser subtask `requires_auth`, run the pipeline, confirm the protected-page test passes and no secret leaks into the portal Evidence tab.
