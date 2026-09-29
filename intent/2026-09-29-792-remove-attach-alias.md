---
status: approved
issue: 792
author: Olaf Krasicki-Freund
---

# Intent: remove the `attach_session_store_after_migrations` alias

Follow-up to #777 (`intent/2026-09-28-777-job-store-after-migrations.md`).

## Problem

#777 renamed the post-migration hook in `apps/backend/plan/service.py` to
`attach_stores_after_migrations()`. It kept the #774 name
`attach_session_store_after_migrations` as an alias for one release, marked
"Remove after 0.6.22". 0.6.22 shipped on 2026-09-28, so the alias is now due to
go.

While it stays:

- the #774 tests in
  `apps/web-server/tests/test_store_after_boot_migrations.py` call the old
  name (four call sites), so they test the alias instead of the hook
  `server/main.py` actually calls;
- CodeQL flags it as an unused global on every PR that touches `service.py`
  (it did on #791 and #794), and each alert needs a manual reply.

## Proposed outcome

- The name `attach_session_store_after_migrations` no longer exists anywhere in
  the code, tests or CFactory's sibling repos (already checked: no caller
  outside PFactory).
- The #774 tests call `attach_stores_after_migrations`.
- No behaviour change: the hook, its return value and its log lines are the
  same.

## Affected users and systems

- `apps/backend/plan/service.py` (one line and its comment).
- `apps/web-server/tests/test_store_after_boot_migrations.py`.
- There are no external callers: `server/main.py` uses the new name since #777,
  and no other repo under `GitHub/` references the old one.

## Constraints

- No functional change and no release on its own. It ships with the next
  release.
- Past artifacts (`intent/`, `spec/`, `plan/`) and `CHANGELOG.md` keep the old
  name; they are history.

## Open questions

Resolved 2026-09-29 (approved): 1 = delete the alias test.

1. **`test_the_old_hook_name_is_an_alias`:** delete it, or turn it into a
   test that the old name is gone (`not hasattr(svc, ...)`)?
   **Recommended: delete it.** A test that a name does not exist guards
   nothing a grep would not, and it would itself keep the old name in the
   code.
