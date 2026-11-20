---
status: draft
issue: 1095
intent: intent/2026-11-20-1095-pf-trusted-plan-submit.md
---

# Spec: trusted plan submit from an agent, with no silent re-plan

## Design

1. **Approval provenance.** `POST /api/plan/sessions/{id}/approve` (`apps/web-server/server/routes/plan_pipeline.py:605`) passes the authenticated principal to `SERVICE.approve` (`apps/backend/plan/service.py:1953`): `approved_by_user_id = request.state.user_id` (None for the shared token) and `auth_kind` (`"user"` or `"shared_token"`). The service stores them on the session's approval record next to the free-text `approver` (display only) and writes an audit event, like the delete route does (`plan_pipeline.py:758-775`). Existing approvals without provenance load as `auth_kind: "unknown"`.
2. **MCP `plan_emit`.** New tool in `apps/backend/agents/tools_pkg/tools/plan_control.py`, registered next to `plan_approve`: `{session_id, repo, project_id, dry_run=true}` → `POST /emit-contract`. A live emit over MCP sends a marker the route can see (header `X-PFactory-Channel: mcp`, set by the MCP http client), and the route refuses it (409, clear message) unless the session is approved with `auth_kind == "user"`. Dry-run is always allowed. The REST route without the marker keeps today's rules, so `parr-run` with the shared token still works.
3. **No silent fallback.** In `apps/backend/plan/emit/contract_emit.py:399-472`, the create-and-run fallback runs only when `PFACTORY_EMIT_FALLBACK=1`. Default: return `{ok: false, errors: [...], aifactory_status, aifactory_detail}` where `aifactory_detail` is the `detail` field of AIFactory's JSON error body (4xx only, truncated to 500 chars); transport exception text stays server-side (Factory#718). A timeout returns `outcome: "unknown"` and tells the caller to check AIFactory before retrying.
4. **Idempotent live emit.** The session records each successful live emit with the contract digest (sha256 of the canonical signed contract). A second live emit with the same digest returns the recorded result with `duplicate: true` and does not POST again; a changed plan (new digest) emits normally.

## Alternatives rejected

- Require a user principal on the REST route too: breaks `parr-run` and the bulk-run scripts that use the shared token today; revisit after #3857 phase 2 issues per-user scoped keys.
- Gate MCP emit on a separate "emit" scope only: a scope says what a key may do, not who approved the plan.
- Keep the fallback but log louder: the re-plan still runs work nobody approved.

## Risks

- Old sessions approved before this change cannot be emitted live over MCP (auth_kind unknown): re-approve, or use the REST path.
- The channel header is set by our own MCP client; a caller faking its absence just gets the REST rules, which are today's rules, so nothing widens.

## Verification

- Unit tests: approve records provenance for a user key and for the shared token; MCP live emit refused for shared-token and unknown approvals, allowed for user approvals; dry-run always allowed; fallback off by default and on with the flag; AIFactory 422 detail surfaced, transport text not; duplicate digest does not POST twice.
- Repo checks: `ruff check`, `ruff format --check`, the ratchet script and the PFactory pytest suite.
- Live (after release): from a Claude session with a user-scoped key, ingest → process → approve → `plan_emit` dry-run then live against aifactory-demo; AIFactory shows the task without a planning phase.
