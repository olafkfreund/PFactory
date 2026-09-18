---
status: approved
issue: 671
spec: spec/2026-09-18-671-process-reject-unknown-fields.md
---

# Plan: `/process` silently drops `repo`/`base_ref`

Approved decisions (from the spec):

- Every JSON request body in `apps/web-server/server/routes/plan_pipeline.py`
  inherits a `_StrictBody` with `model_config = ConfigDict(extra="forbid")`:
  IngestTextBody, FromIssueBody, ApproveBody, RejectBody, DiscardBody,
  WaiveBody, ApproveAccessBody, EmitBody, EmitContractBody, PlanUpdateBody,
  AcceptedSuggestion, ApplySuggestionsBody. Unknown field → 422 naming it.
- Bare `POST /process` (no body) keeps working.
- No late-binding repo on `/process`.
- Audited clients all conform except the user's global
  `~/.claude/skills/parr-run/SKILL.md:117`, which sends `auto_restart:false` to
  `/approve`; that key is deleted there (outside this repo; approved).
- Release note for the behaviour change.

## Steps

1. `plan_pipeline.py`: add `ConfigDict` import and `_StrictBody`; switch the 12
   classes' base from `BaseModel` to `_StrictBody`. → verify by
   `git grep -c "(BaseModel)" plan_pipeline.py` = 1 (the base itself).
2. `tests/test_plan_process_updates_route.py`: add
   - `{"repo": "o/r", "base_ref": "main"}` on `/process` → 422, detail names
     `repo`, and the service's process/update was not called;
   - one unknown key on `/emit-contract` → 422.
   → verify by running green.
3. Negative control: drop `extra="forbid"`, the new tests fail; restore.
4. `CHANGELOG.md`: one line under the unreleased section — plan-session routes
   now reject unknown fields with 422.
5. `~/.claude/skills/parr-run/SKILL.md:117`: remove `, auto_restart:false`
   from the approve body. → verify by grep. (Not part of the repo diff.)

## Tests

    apps/backend/.venv/bin/pytest tests/test_plan_process_updates_route.py tests/test_plan_from_issue_route.py \
      tests/test_access_approve_route.py tests/test_docs_targets_route.py \
      tests/test_plan_write_routes_tenant_guard.py tests/test_mcp_plan_tools_use_http.py -q
    (cd apps/frontend-web && npx vitest run src/lib/__tests__/planning-api.test.ts)

Expected: all pass.

## Rollback

Revert the commit (routes go back to ignoring unknown fields). The `parr-run`
edit is harmless either way — PFactory never read `auto_restart`.
