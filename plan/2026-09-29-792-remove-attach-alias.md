---
status: approved
issue: 792
spec: spec/2026-09-29-792-remove-attach-alias.md
---

# Plan: remove the `attach_session_store_after_migrations` alias

Self-contained summary of the approved decisions:

- `apps/backend/plan/service.py`: delete the alias line
  `attach_session_store_after_migrations = attach_stores_after_migrations`
  and its comment "#777 renamed the hook; ... Remove after 0.6.22." (lines
  571-572), leaving two blank lines before `class PlanService`.
- `apps/web-server/tests/test_store_after_boot_migrations.py`:
  - rename the four calls (lines 130, 139, 154, 157) to
    `attach_stores_after_migrations`;
  - delete `test_the_old_hook_name_is_an_alias` (lines 187-189) with the
    blank lines before it.
- No "name is gone" test and no deprecation warning.
- The hook, its return value, its log lines and `server/main.py` are
  unchanged.
- Past artifacts and `CHANGELOG.md` keep the old name as history.
- No release on its own; it ships with the next release.

All work happens in the worktree `/tmp/.../scratchpad/pf-792` on
`chore/792-remove-attach-alias`. The backend `.venv` is symlinked into it and
never staged (#786).

## Steps

1. **`service.py`:** delete the alias and its comment.
   → verify: `python -c "import plan.service as s; assert not hasattr(s, 'attach_session_store_after_migrations')"`
   (with `PYTHONPATH=apps/backend`).
2. **The test file:** rename the four calls and delete the alias test.
   → verify: `git grep attach_session_store_after_migrations -- ':!intent' ':!spec' ':!plan' ':!CHANGELOG.md'`
   returns nothing, and the file's tests show 3 passed.
3. **Gates:**
   - the ci.yml per-process suites: `pytest tests/ apps/web-server/tests/ -m "not slow"`;
   - `scripts/ratchet_lint.py --base origin/dev --package apps/backend
     --package apps/web-server --package scripts`, run after the commit;
   - `uvx ruff@0.15.17 format --check` on the two files.

   Commit with the hook, never `--no-verify`, staging only the two named
   files.
   → verify all green.
4. **PR** to `dev` with `Fixes #792`, linking the intent, spec and plan.
   Merge (merge commit) when the required checks are green and review threads
   are addressed.
   → verify #792 closes, and CodeQL alert 1779 closes as fixed (if it does
   not, dismiss it as fixed).

## Tests

```bash
cd <pf-792 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
pytest apps/web-server/tests/test_store_after_boot_migrations.py -q   # 3 passed
pytest tests/ apps/web-server/tests/ -m "not slow" -q                  # green
python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

Revert the commit on `dev`. That restores the alias; there is no schema,
config or behaviour change to undo.
