---
status: approved
issue: 1095
spec: spec/2026-11-20-1095-pf-trusted-plan-submit.md
coders:
  1: codex   # small tricky auth/provenance logic
  2: codex   # tool + route gate, same context as step 1
  3: codex   # fallback removal with error-leak rule (Factory#718)
  4: codex   # digest/idempotency logic
reviewers:
  - codex    # independent read of the full diff
  - agy      # second vendor review (owner re-enabled agy 2026-11-20)
---

# Plan: trusted plan submit from an agent, with no silent re-plan

Approved decisions (from the spec): approvals record the authenticated principal (`request.state.user_id`, `auth_kind` user|shared_token|unknown) server-side; new MCP tool `plan_emit` (dry-run default); a live emit marked as MCP (`X-PFactory-Channel: mcp`) needs a `user` approval, REST without the marker keeps today's rules; the create-and-run fallback runs only with `PFACTORY_EMIT_FALLBACK=1`, otherwise return AIFactory's 4xx `detail` (truncated 500) and never transport exception text; a repeated live emit with the same contract digest returns the recorded result with `duplicate: true`.

## Steps

1. `apps/web-server/server/routes/plan_pipeline.py:605-622` and `apps/backend/plan/service.py:1953-1963` (+ the approval record in `apps/backend/plan/review/` where `approve_review` stores approver): pass `user_id` and `auth_kind` from the route, store them on the approval, write an audit event (pattern: `plan_pipeline.py:758-779`, then `await db.commit()`). Loading an old approval yields `auth_kind: "unknown"`.
   → verify: new tests in `tests/` for user key, shared token, old session.
   Traps: `request.state.user_id` is None for the shared token; keep `ApproveBody` field names (MCP `plan_approve` and parr-run send `approver`).
2. `apps/backend/agents/tools_pkg/tools/plan_control.py` (next to `plan_approve`, ~line 248) and its http client (`apps/backend/agents/tools_pkg/http_client.py`): add `plan_emit` and send `X-PFactory-Channel: mcp` on every MCP call. In `emit_contract` route (`plan_pipeline.py:854`): if header is `mcp` and not `dry_run`, require session approved with `auth_kind == "user"`, else 409 with a clear message. Register the tool in the standalone MCP server tool list.
   → verify: tests for refused (shared_token, unknown), allowed (user), dry-run always allowed; tool appears in the MCP server's tool list test.
   Traps: plan tools are registered only in the standalone MCP server (module docstring, lines 10-16); update that docstring.
3. `apps/backend/plan/emit/contract_emit.py:399-472`: run the fallback only when `os.environ.get("PFACTORY_EMIT_FALLBACK") == "1"`. Default return `{ok: False, dry_run: False, endpoint, errors, aifactory_status, aifactory_detail}`; on timeout `outcome: "unknown"`. Extract `detail` only from an HTTP 4xx JSON body.
   → verify: tests for fallback off/on, 422 detail surfaced, transport error text absent from the response.
   Traps: Factory#718 (never echo exception text); `_post_with_readback` raises for the fallback today, keep its retry behaviour.
4. `apps/backend/plan/service.py` `emit_contract` (~line 2244, inside the existing `_emit_lock`): compute sha256 of the canonical signed contract; on a successful live emit store `{digest, result, at}` on the session; a later live emit with the same digest returns the stored result plus `duplicate: True` without calling `http`.
   → verify: test with a fake http that counts POSTs (1 after two identical emits, 2 after a changed plan).
   Traps: dry-run must not record; keep the lock and StaleSessionError handling.
5. Docs: `guides/agent-handoff.md` — add `plan_emit`, the user-key rule and `PFACTORY_EMIT_FALLBACK`; fix the stale port/command there.
   → verify: markdown renders; links resolve.

## Tests

`ruff check apps tests`, `ruff format --check apps tests`, the repo ratchet script, and `pytest tests -q` (full PFactory suite) all green.

## Rollback

Revert the PR. Setting `PFACTORY_EMIT_FALLBACK=1` restores the old fallback without a revert. Approval provenance fields are additive.

## Deviations (recorded during implementation)

- Step 1: this branch exposes the authenticated principal as `request.state.user` (dict with `id`, `is_service`), not `request.state.user_id`; provenance derives from it. Service principals count as `shared_token`.
- Step 2: the stdio MCP client calls go through the `/api/mcp-stdio/*` proxy, so a proxy route `POST /api/mcp-stdio/plan/sessions/{id}/emit-contract` and audit action `ACTION_MCP_PLAN_EMIT` were added to make `plan_emit` reachable through the existing scoped path.
