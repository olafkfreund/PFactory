---
status: approved
issue: 792
intent: intent/2026-09-29-792-remove-attach-alias.md
---

# Spec: remove the `attach_session_store_after_migrations` alias

Decision carried from the approved intent: delete
`test_the_old_hook_name_is_an_alias`; do not replace it with a "name is gone"
test.

## Design

### Facts from the code (`origin/dev`, 2026-09-29)

`git grep attach_session_store_after_migrations`, excluding `intent/`,
`spec/`, `plan/` and `CHANGELOG.md`, finds six lines in two files:

- `apps/backend/plan/service.py:571-572`: the alias and its comment "#777
  renamed the hook; the #774 name stays as an alias. Remove after 0.6.22."
- `apps/web-server/tests/test_store_after_boot_migrations.py`:
  - lines 130, 139, 154 and 157 call the old name (in
    `test_attach_after_migrating_uses_the_store_and_imports` and
    `test_the_replica_guard_waits_for_migrations`);
  - line 189 is `test_the_old_hook_name_is_an_alias` (lines 187-189 with
    its comment).

`server/main.py` already calls `attach_stores_after_migrations`. No docs
(`guides/`, `docs/`) name the old hook, and no sibling repo under `GitHub/`
references it.

### The change

- `service.py`: delete the alias line, its comment and the blank lines
  around it, so `attach_stores_after_migrations` is followed directly by
  `class PlanService`, with the usual two blank lines between them.
- The test file: rename the four calls to `attach_stores_after_migrations`,
  and delete `test_the_old_hook_name_is_an_alias` with its comment and the
  blank lines before it.

Nothing else changes: not the hook, its return value, its log lines, nor
`main.py`.

## Alternatives rejected

- **Keep the alias longer:** nothing calls it, and it costs a CodeQL reply on
  every PR that touches `service.py`.
- **Replace the alias test with `not hasattr(svc, "...")`:** rejected in the
  intent; a grep guards the same thing, and the test would keep the old name
  in the code.
- **A deprecation warning instead of removal:** there is no external caller
  left to warn.

## Risks

- **An out-of-tree caller breaks with `AttributeError`.** None exists in
  PFactory or its sibling repos, and the hook is internal to the web server's
  lifespan. If one appears, the fix is to use the new name.
- **CodeQL alert 1779** should close as fixed when this merges. If it does not
  close by itself, dismiss it as fixed.

## Verification

- `git grep attach_session_store_after_migrations` (excluding `intent/`,
  `spec/`, `plan/` and `CHANGELOG.md`) returns nothing.
- `pytest apps/web-server/tests/test_store_after_boot_migrations.py`: 3 passed
  (the 4 from #777 minus the deleted alias test).
- The ci.yml per-process suites stay green.
- `scripts/ratchet_lint.py` in CI form (three `--package` flags) passes.
- `ruff 0.15.17` format check passes on the two files.
- Commit with the hook from a worktree with the backend `.venv` symlinked
  (#786).
- There is no DB schema or store change, so the Postgres and store-mode runs
  are not needed.
