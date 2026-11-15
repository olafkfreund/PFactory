---
status: draft
issue: 996
spec: spec/2026-11-14-996-intake-attribution.md
coders:
  1: coder   # plain field threading along an existing pattern (org_id), with its tests
  2: codex   # small tricky logic: the attribution rules and the from-issue exception
reviewers:
  - codex    # independent read of the diff against this plan
  - agy      # second independent read; asked for by the 2026-11-14 team brief
---

# Plan: originator and agent identity on plan sessions and in the run signature

PFactory#996, part of olafkfreund/Factory#1960. Base branch `dev`.

## Approved decisions (from the spec; nothing else is in scope)

- A plan session records two nullable strings at intake, `originator` and `agent_identity`, and the run signature emits them instead of the constant `None`.
- They follow the path `org_id` takes: resolved in the route, passed to `PlanService` as keywords, stamped on the session, read at emit.
- `originator` is the caller's opaque user id (`user["id"]`): only when it is a `str`, non-blank after `strip()`, printable, the principal is not `is_service`, and the id is not `"default"`. **Never the email.**
- `agent_identity` is `agent:pfactory/<api_key_id>` when `user["api_key_id"]` is a non-blank printable `str`. Independent of `originator`.
- From-issue records `agent_identity` but always a null `originator` (the originator of an issue is its author, which PFactory is not sent yet).
- `plan/agent_api.py` `plan_ingest` and `routes/github.py` `plan_review_pr` are **not** touched; they record null through the keyword defaults.
- Null when unknown. No placeholder, no default string, no coercion (`str(x)`) of a non-string id.
- `SIGNATURE_SCHEMA` stays `2`. No migration: sessions persist as pydantic JSON, and old payloads load with both null.
- Do not touch `apps/web-server/server/auth.py`, `websockets/`, `mcp_stdio/`, the key model, or `FromIssueBody`.

## Steps

1. **Session fields, service keywords, signature** (coder).
   - `apps/backend/plan/service.py:109`: after `org_id` on `PlanSession`, add `originator: str | None = None` and `agent_identity: str | None = None`, with one comment line (`# Factory#1960: who started the session, stamped at intake; None = unknown.`).
   - `apps/backend/plan/service.py:898-931` (`ingest_text`) and `:933-966` (`ingest_bytes`): add keyword-only `originator: str | None = None, agent_identity: str | None = None` after `org_id`; stamp `session.originator = originator or None` and `session.agent_identity = agent_identity or None` right after the `session.org_id = ...` line (919, 959), before `self._save(session)`.
   - `apps/backend/plan/emit/docs/render.py:166-167`: `"originator": session.originator,` and `"agent_identity": session.agent_identity,`.
   - Tests: in `tests/test_plan_docs_run_signature.py` add a test that ingests through `service.ingest_text(..., originator="u-1", agent_identity="agent:pfactory/k-1")`, processes, and asserts both in `render_plan_docs(...).run_signature`; leave `test_governance_fields_are_honest_when_absent` as is. In `tests/test_plan_service.py` add: `ingest_bytes` with both keywords stamps both; a session with both set survives `PlanSession.model_validate_json(session.model_dump_json())`; a dumped payload with the two keys removed reloads with both `None`.
   → verify, from the worktree root: `apps/backend/.venv/bin/pytest tests/test_plan_docs_run_signature.py tests/test_plan_service.py tests/test_governance_cli.py -q`. The venv is the worktree's own (`apps/backend/.venv`, built as CI builds it: `uv venv`, the three requirements files, `ruff==0.15.17`, `mypy==1.20.1`, the type stubs). Do not use the main checkout's venv: it is stale (mcp 1.28, the branch needs >=2.0), and the pre-commit hook prefers the worktree's venv when one exists.
   Traps: `summary()` deliberately omits `org_id`; do not add the new fields to it. Do not bump `SIGNATURE_SCHEMA`. Do not edit the governance fixtures under `tests/fixtures/governance/`.

2. **Attribution helper and the three ingest routes** (codex).
   - `apps/web-server/server/routes/plan_pipeline.py`, directly after `_user` (line 197-198): add
     `def _attribution(user: object, *, authored_elsewhere: bool = False) -> dict[str, str | None]` implementing exactly the rules under "Approved decisions". Read with `.get()`. Return `{"originator": None, "agent_identity": None}` for a non-dict. One short docstring; no logging.
   - Same file: pass `**_attribution(_user(request))` to `SERVICE.ingest_text` in `ingest_text` (after `org_id=org_id`, line 290) and to `SERVICE.ingest_bytes` in `ingest_upload` (line 364); pass `**_attribution(_user(request), authored_elsewhere=True)` in `ingest_from_issue` (line 330).
   - Tests in a new `tests/test_plan_intake_attribution.py`:
     - `_attribution` unit cases: JWT-shaped user `{"id": "u-1", "email": "a@b.c", "role": "user"}` gives `originator == "u-1"`, agent null, and the email appears nowhere in the result; key user `{"id": "u-1", "api_key_id": "k-1", "org_id": "o-1"}` gives both; `{"id": "default", "is_service": True}` gives both null; a service principal with an `api_key_id` gives a null originator and the agent; `authored_elsewhere=True` gives a null originator and keeps the agent; `None`, a non-string id (`7`), `""`, `"   "` and `"a\nb"` give null for that value.
     - Route cases, calling the handlers directly with `asyncio.run` as `tests/test_plan_from_issue_route.py:55-77` does (same `service` and `ensure_tracked_project` fixtures), with a request stub that has `headers` **and** `state.user`: `ingest_text` stores both on the session; `ingest_upload` stores both; `ingest_from_issue` stores the agent and `originator is None`.
   → verify: `pytest tests/test_plan_intake_attribution.py tests/test_plan_from_issue_route.py tests/test_plan_session_org_scope.py tests/test_mcp_stdio_proxy.py -q`.
   Traps: no `TestClient` (the auth middleware opens its own DB session; dependency overrides do not reach it). With a keyed `state.user`, `_ingest_org` calls `authorize_plan_ingest`, which may reach the DB: in the route tests monkeypatch `pp._ingest_org` to an async function returning `None`. `ingest_upload` takes an `UploadFile` and `Form` defaults: pass every form argument explicitly (`title=None, category="", template="", repo=None, base_ref=None, db=None`) and a stub file object with an async `read()` and a `filename`. `request.state.user` is absent on the existing header-only stub; `_user` already tolerates that, keep it so.

3. **Checks, review, PR** (lead).
   - `apps/backend/.venv/bin/ruff check` and `ruff format --check` (config `ruff.toml`) on touched files.
   - The ratchet as CI runs it, with the venv's `bin` first on `PATH`: `apps/backend/.venv/bin/python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts`.
   - The test commands above plus `tests/test_ingest_channels.py tests/test_tenant_scoping.py`.
   - The postgres suite, which skips without a database and which CI runs: start a throwaway `postgres:16` container (user, password and database `pfactory_test`, a free local port), then `TEST_POSTGRES_URL=postgresql+asyncpg://pfactory_test:pfactory_test@localhost:<port>/pfactory_test apps/backend/.venv/bin/pytest tests/postgres/ -m postgres -q`; remove the container afterwards. Expected: no skips for a missing database, no failures.
   - Codex and agy read the diff against this plan; a fresh Fable agent does the final review (at most 2 rounds). Open the PR to `dev`.

## Tests

All commands in steps 1-3 pass; no existing test is edited except by addition.

## Rollback

Revert the PR. Sessions written meanwhile carry two extra JSON keys; `PlanSession` uses pydantic's default `extra="ignore"`, so the reverted code loads them. (Step 1 confirms `PlanSession` does not set `extra="forbid"`; if it does, say so and stop.)
