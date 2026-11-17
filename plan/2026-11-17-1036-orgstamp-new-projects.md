---
status: draft
issue: 1036
spec: spec/2026-11-17-1036-orgstamp-new-projects.md
coders:
  1: you (lead)
  2: you (lead)
reviewers:
  - codex
---

# Plan: stamp the creator's org on new projects

Approved spec decisions: `creator_org` helper (key org, sole membership, else None+log);
`add_project` -> `create_project(project, org_id)`; MCP proxy passes the key org;
PFactory `ensure_tracked_project(repo, org_id)` stamps new records only; TFactory
`ingest_spec` stamps self-registered projects; agent tool unchanged. Both repos, base dev.
Steps are small and edit few files per repo; the lead implements them (Codex reviews).

## Steps

1. PFactory `apps/web-server/server/routes/{project_authz,projects,plan_pipeline}.py`,
   `mcp_stdio/router.py`: add `creator_org`; split `create_project`; thread `org_id`
   through `ensure_tracked_project` and the three ingest routes; MCP proxy passes key org.
   Update stubs/tests that mock `ensure_tracked_project` or call `add_project`.
   Add `tests/test_project_org_stamp.py` -> `pytest tests/test_project_org_stamp.py
   tests/test_task_route_rbac.py tests/test_mcp_stdio_proxy.py apps/web-server/tests -q`.
   Traps: lint ratchet is diff-scoped; use the repo's ruff, never `ruff --fix` repo-wide.
2. TFactory `apps/web-server/server/routes/{project_authz,projects,specs}.py`,
   `mcp_stdio/router.py`: same helper and split; stamp in `_clone_and_register_project`.
   Add `tests/test_project_org_stamp.py` -> `pytest tests/test_project_org_stamp.py
   tests/test_specs_ingest_clone.py tests/test_contract_signature.py -q`.
   Traps: `ingest_spec` called directly in tests, so `request` defaults to None.

## Tests

Both repos: the commands above, then `ruff` ratchet via `scripts/ratchet_lint.py --base origin/dev`.

## Rollback

Revert the PRs. Stamped `org_id` values are harmless to older code (the key already exists).
