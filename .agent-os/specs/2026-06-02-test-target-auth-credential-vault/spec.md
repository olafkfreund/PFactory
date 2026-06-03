# Spec Requirements Document

> Spec: Test-Target Authentication + Credential Vault
> Created: 2026-06-02
> Status: Planning
> Tracking issue: [#107](https://github.com/olafkfreund/PFactory/issues/107)

## Overview

Let PFactory **log in to a system-under-test and reuse that session** when generating and running tests, by storing test-target credentials encrypted at rest and injecting them into the sandbox only for egress-enabled lanes. This unblocks testing anything behind interactive auth — SaaS apps, CRMs (ServiceNow/SAP/Salesforce, [#111](https://github.com/olafkfreund/PFactory/issues/111)), and real staging environments — and is a prerequisite for first-class Kubernetes app testing ([#108](https://github.com/olafkfreund/PFactory/issues/108)).

## Background — what already exists (≈70% of the plumbing)

This spec is mostly **assembly**, not greenfield. The reusable pieces:

- **Encrypted-at-rest storage pattern** — `GitCredential` + `_EncryptedString` (KMS/Vault-backed) in `apps/web-server/server/database/models.py:383`; CRUD in `apps/web-server/server/routes/git_credentials.py` (token never returned post-create, org-scoped).
- **Secrets broker** — `apps/backend/pfactory_secrets/broker.py` (`resolve_ref("env:NAME" | "vault:path#field")`, `apply_to_env()`, `close()` wipes materialised files).
- **Sandbox credential injection** — `apps/backend/tools/runners/sandbox_credentials.py:resolve_sandbox_credentials(project_dir, spec_dir, network)` (egress-gated; materialises files 0600, read-only bind-mounts, `wipe()`).
- **Egress gating** — `apps/backend/pfactory_secrets/egress.py:egress_enabled(project_dir)` (off by default).
- **Env injection into the container** — `apps/backend/tools/runners/docker_runner.py` (`extra_env` → `-e KEY=VAL`, `secret_files` → read-only mounts). Browser/API lanes already run `network="host"` with a `target_url` (`apps/backend/agents/evaluator.py:751,1228`).
- **`.pfactory.yml` target auth** — `apps/backend/pfactory_yml/schema.py` already supports `http` targets with `auth: {type: bearer, token_env}`.

## What's missing (the work)

1. A **test-target credential store** (username/password/API key/TOTP for the SUT) — there is none today.
2. A `.pfactory.yml` **`test_credentials:`** block referencing broker refs.
3. A broker entry point — **`resolve_test_target_credentials()`** — wired into the executor for egress lanes.
4. A real Playwright **login fixture + `storageState`** (login once, reuse across a spec file). Today `frameworks/playwright/templates/login-flow.spec.ts.tmpl` hardcodes fake creds (`requires_auth: false`).
5. Planner/Gen-Functional awareness of **`requires_auth`** subtasks.

## User Stories

### Operator stores a test credential
As a **PFactory operator**, I want to store my staging app's username/password (or API token) encrypted in the portal and reference it from `.pfactory.yml`, so generated tests can authenticate without me pasting secrets into test files.

### Generated test logs in once and reuses the session
As an **engineer**, I want a generated Playwright test to log in a single time and reuse the authenticated session across the file, so protected pages are testable and the suite isn't N separate logins.

### Secrets never leak
As a **security owner**, I want test-target secrets encrypted at rest, injected only into egress-enabled lanes, wiped after each run, and never written to logs/artifacts/triage reports, so storing creds in PFactory is safe.

## Spec Scope

1. **`TestTargetCredential` model + migration** — encrypted, org-scoped, never returned post-create (mirrors `GitCredential`).
2. **Portal CRUD** — `POST/GET/DELETE /api/test-credentials` + a Settings UI panel.
3. **`.pfactory.yml` `test_credentials:` schema** — name → backend ref (`env:` / `vault:` / `store:<id>`) → injected env var name; `requires_auth` linkage on targets.
4. **`resolve_test_target_credentials()`** in `pfactory_secrets` + wiring into `sandbox_credentials` / the executor for browser/api lanes.
5. **Playwright auth fixture + `storageState`** + a rewritten `login-flow` template that reads injected env vars.
6. **Planner/Gen-Functional `requires_auth`** awareness so auth-needing subtasks get the fixture + egress.

## Out of Scope

- Platform-specific connectors (ServiceNow Table API, SAP OData, Salesforce SOQL) — that's [#111](https://github.com/olafkfreund/PFactory/issues/111), which builds on this.
- Full SSO/SAML IdP automation (interactive MFA, hardware keys). v1 covers form login + API token + TOTP seed; federated SSO is a follow-on.
- Kubernetes port-forward execution — [#108](https://github.com/olafkfreund/PFactory/issues/108) (consumes this spec's creds).
- Secret rotation/lifecycle management beyond store/use/delete.

## Expected Deliverable

1. An operator can store a SUT credential (encrypted) via the portal and reference it in `.pfactory.yml`.
2. A generated Playwright test authenticates via `storageState` and asserts on a protected page, in the sandbox, with creds injected only for the egress lane and wiped after.
3. No plaintext credential appears in any log, artifact, verdict, or triage report (verified by test).
4. `npm run typecheck` (frontend) + the backend `critical` lane + new unit/integration tests all green.
