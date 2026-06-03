# Test-target authentication — log in, then test

> PFactory can store credentials for a **system-under-test** (a staging app, a
> SaaS/CRM, anything behind a login) encrypted at rest, and inject them into the
> sandbox **only** for egress-enabled lanes so generated tests can authenticate
> — without secrets pasted into test files. Resolution is **off by default** and
> gated by the same per-project egress opt-in as the credential broker.
>
> Epic [#107](https://github.com/olafkfreund/PFactory/issues/107).
> Spec: `.agent-os/specs/2026-06-02-test-target-auth-credential-vault/`.
> Distinct from the **infra** [Credential Broker](./credentials.md) (#62), which
> authenticates *agents* to cloud environments — this is for the *test target*.

## TL;DR

```text
1. Store the credential (encrypted, org-scoped) — portal Settings or the API:
     POST /api/test-credentials  { name, kind, username, secret }   → returns an id (tc_…)

2. Reference it from .pfactory.yml (egress MUST be enabled):
     egress: { enabled: true }
     test_credentials:
       login: { ref: store:tc_…, as_secret: TEST_PASSWORD, as_username: TEST_USERNAME }
     targets:
       - name: web
         type: http
         base_url: https://staging.example.com
         auth: { type: ref, ref: login, login_url: /login, ... selectors ... }

3. A subtask that needs auth is tagged `requires_auth: true`; the browser lane
   logs in once (Playwright storageState) and reuses the session.
```

## Where credentials come from (the `ref`)

Each `test_credentials` entry names a secret **reference**; the resolved value
becomes the `as_secret` env var inside the sandbox (and `username_ref` →
`as_username`):

| Scheme | Resolved by | Example |
|---|---|---|
| `store:<id>` | the **web-server** (it owns the encrypted DB) | `store:tc_a1b2` |
| `env:NAME` | the credential broker | `env:STAGING_PW` |
| `vault:path#field` | the credential broker | `vault:secret/staging/app#password` |

> The backend agent runs in a separate venv without the DB driver, so
> `store:<id>` refs are resolved web-server-side at hand-off and arrive as
> plain env; `env:`/`vault:` refs are resolved by the backend broker.

## Storing a credential

The portal stores test-target credentials **encrypted at rest** (the same
`EncryptedString` / KMS·Vault·Azure·GCP backends as Git credentials). The
secret is **never returned by the API after creation** — only metadata.

```bash
curl -sX POST http://localhost:3102/api/test-credentials \
  -H 'Content-Type: application/json' \
  -d '{"name":"staging-login","kind":"form","username":"qa@acme.test","secret":"•••••"}'
# → {"id":"tc_a1b2","name":"staging-login","kind":"form","username":"qa@acme.test", ...}
```

`kind` is one of `form` · `api_token` · `basic_auth` · `totp`.

## `.pfactory.yml` schema

```yaml
version: 1

egress:
  enabled: true            # REQUIRED — login needs network egress (fail-closed otherwise)

test_credentials:
  login:
    ref: store:tc_a1b2     # store:<id> | env:NAME | vault:path#field
    kind: form
    as_secret: TEST_PASSWORD     # env var the secret is exposed as
    as_username: TEST_USERNAME   # optional: env var for the username
    # username_ref: env:STAGING_USER   # optional: resolve the username from a ref

targets:
  - name: web
    type: http
    base_url: https://staging.example.com
    auth:
      type: ref            # references a test_credentials entry by name
      ref: login
      login_url: /login
      username_selector: "#email"
      password_selector: "#password"
      submit_selector: "button[type=submit]"
      success_url_pattern: "**/dashboard"
```

Validation is **fail-closed**: declaring `test_credentials` without
`egress.enabled` is an error, and a `ref`-auth must name a declared entry.

## Security model

- **At rest:** encrypted via `EncryptedString` (KMS / Vault / Azure KV / GCP SM).
- **In flight:** injected only as ephemeral container env (`-e`) for
  egress-enabled lanes; **wiped after every run**; hermetic lanes (unit /
  mutation, `--network=none`) get nothing.
- **No leakage:** resolved secret values are scrubbed from logs / junit / the
  HAR / verdicts / triage by the `Redactor` (`pfactory_secrets/redaction.py`).
- **Never returned:** the portal API returns credential metadata only.

## Status

The credential **vault** and config surface are implemented and tested:

| Piece | Status |
|---|---|
| Encrypted storage + `/api/test-credentials` | ✅ (#107 task 1) |
| Backend broker resolver (`env:`/`vault:`) | ✅ (task 2) |
| `.pfactory.yml` `test_credentials` + ref-auth + `requires_auth` | ✅ (task 3) |
| Web-server `store:` resolver | ✅ (task 4a) |
| Schema → resolver spec glue | ✅ (task 4b-glue) |
| **Executor injection + sink redaction wiring** | ⏳ pending (task 4b-final) |
| **Playwright `storageState` login fixture** | ⏳ pending (task 5) |
| **Portal Settings UI** | ⏳ pending (task 7) |

Until the remaining steps land, declaring `test_credentials` is parsed and
validated but not yet consumed end-to-end by a running pipeline. Track progress
on [#107](https://github.com/olafkfreund/PFactory/issues/107).
