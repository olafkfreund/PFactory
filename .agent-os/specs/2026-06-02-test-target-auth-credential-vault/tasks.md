# Spec Tasks

These are the tasks for the spec in @.agent-os/specs/2026-06-02-test-target-auth-credential-vault/spec.md

> Created: 2026-06-02
> Status: Ready for Implementation
> Tracking issue: #107

Phased so each parent task is independently shippable behind the egress/`requires_auth` opt-ins (default off → zero behaviour change until wired end-to-end).

## Tasks

- [x] 1. **Credential storage (web-server)** — PR for #107 (this branch)
  - [x] 1.1 Write tests for `TestTargetCredential` (encryption-at-rest round-trip + ciphertext-at-rest, `UNIQUE(org_id,name)`, no-secret response shape) — `tests/secrets/test_test_target_credential.py`
  - [x] 1.2 Add the model (mirrors `GitCredential`, + `username`/`extra`/unique) + Alembic migration `c3e5a8b1d2f4` (chains off `b2d4f7e9c3a1`)
  - [x] 1.3 `POST/GET/DELETE /api/test-credentials` (metadata-only response, org-scoped authz mirroring `git_credentials.py`); router registered in `main.py`
  - [x] 1.4 Migration applies on `postgres (P1)` PG 15 + 16 via CI; new-credential tests run in the `secrets (P2)` CI job

- [x] 2. **Resolver** (`tools/runners/sandbox_credentials.py`) — PR for #107
  - [x] 2.1 Tests: `tests/test_resolve_test_target_credentials.py` (hermetic→none, no-specs→none, egress-off→none, env/username refs, multi-spec, fault-tolerant skip)
  - [x] 2.2 `resolve_test_target_credentials(specs, project_dir, spec_dir, network)` + `TargetCredentialSpec`, mirroring `resolve_sandbox_credentials`; resolves broker refs (env/vault/cloud), gated on egress.
        **Deviation from the spec's Option A:** the backend agent runs in a
        venv WITHOUT the DB driver, so it cannot directly decrypt `store:<id>`.
        `store:` is therefore materialised **web-server-side** (it owns the DB)
        and arrives as an `env:` ref; the backend resolver skips any stray
        `store:` ref. The web-server `store:` materialisation moves to task 4.
  - [x] 2.3 Added `test_resolve_test_target_credentials` to `CRITICAL_MODULES` (214 tests, ~7s)

- [x] 3. **`.pfactory.yml` schema + subtask field** — PR for #107
  - [x] 3.1 Tests: `tests/test_pfactory_yml_test_credentials.py` (parse, ref-auth, fail-closed, env-name, subtask round-trip)
  - [x] 3.2 `pfactory_yml/schema.py`: `TestCredentialEntry` + `test_credentials` map + `RefAuth` (`type: ref`) in the auth union; `test_plan/subtask.py`: `requires_auth: bool = False` (wired through `to_dict`/`from_dict`, omitted at default)
  - [x] 3.3 `model_validator` fails closed: `test_credentials` without `egress.enabled` → error; `auth.ref` must name a declared entry. Added module to `CRITICAL_MODULES` (221 tests)

- [~] 4. **Executor wiring + redaction** — split into 4a (done) + 4b (pending)
  - [x] 4a **store: resolver (web-server)** — `server/services/test_credential_resolver.py`
        `resolve_store_credential(db, id) → (username, secret)` (decrypts on read,
        bumps `last_used_at`) + `parse_store_ref`. Test in the `secrets` job.
        This is the web-server half deferred from task 2 (backend venv has no DB).
  - [x] **Finding:** the redaction *primitive* already exists —
        `pfactory_secrets/redaction.py` (`Redactor` value-scrub + `RedactingFilter`
        + `scrub_patterns`). 4b is *wiring* it, not new code.
  - [x] 4b-glue **`config_to_credential_specs(config, target_name)`** —
        bridges the task-3 schema to task-2 specs (ref-auth target → resolver
        spec). Pure + critical-lane tested (`tests/test_config_to_credential_specs.py`).
  - [ ] 4b.1 Wire `agent_service` hand-off to expand `store:` refs (via 4a) +
        `env:`/`vault:` refs (via task-2 resolver) into the run env for egress lanes
        ⚠️ **live `agent_service.py` — needs a real pipeline run to verify**
  - [ ] 4b.2 In the Evaluator's egress lanes call `config_to_credential_specs` →
        `resolve_test_target_credentials` → merge into `extra_env`; seed a
        `Redactor` from the values; apply at the sinks (logs, junit, HAR
        `http_recorder.py` ← highest risk, verdicts, triage)
        ⚠️ **live `evaluator.py` critical path — pipeline-verify before merge**
  - [ ] 4b.3 Tests: executor injects creds for egress lanes only; hermetic gets
        nothing; secret scrubbed from every artefact

- [ ] 5. **Playwright auth (`storageState`)**
  - [ ] 5.1 Write a runner smoke test: `auth.setup.ts` logs in once → `state.json`; protected test reuses it (no second login in HAR)
  - [ ] 5.2 Add `auth.setup.ts.tmpl` + update `playwright.config.tmpl.ts` (setup project + `use.storageState`, gated on `requires_auth`)
  - [ ] 5.3 Rewrite `frameworks/playwright/templates/login-flow.spec.ts.tmpl` to read injected env vars
  - [ ] 5.4 Verify the smoke test passes in the playwright runner image

- [ ] 6. **Planner / Gen-Functional `requires_auth`**
  - [ ] 6.1 Write tests: Planner tags auth subtasks; Gen-Functional emits storageState-backed tests; Evaluator runs setup first
  - [ ] 6.2 Implement the tagging + generation path (prompt + context_block updates)
  - [ ] 6.3 Verify all tests pass

- [ ] 7. **Portal UI**
  - [ ] 7.1 Settings → Credentials panel (create/list-metadata/delete) reusing the Git-credentials UI pattern; i18n en/fr/pt-BR
  - [ ] 7.2 `npm run typecheck` + component tests; verify green

- [x] 8. **Docs + guide** — PR for #107 (does not need a pipeline)
  - [x] 8.1 `guides/test-target-auth.md` — store → reference in `.pfactory.yml`
        → `requires_auth`; ref schemes; security model; honest **status** table.
  - [x] 8.2 `.pfactory.yml.example` gains a commented `test_credentials` + ref-auth
        block. (Keep #107 **open** until the pipeline-verified tasks 4b-final/5/7 land.)

## Definition of done
- All acceptance criteria in `spec.md` met; no plaintext secret in any log/artifact/verdict/triage (redaction test green).
- Defaults unchanged: hermetic lanes get no creds; the whole feature is opt-in via egress + `requires_auth`.
- Backend `critical` + `postgres` CI lanes + frontend typecheck green.
