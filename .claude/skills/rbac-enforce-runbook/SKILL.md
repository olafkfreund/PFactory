---
name: rbac-enforce-runbook
description: Follow the PFactory runbook for checking legacy acw_ keys before enforcing task-route RBAC.
when_to_use: When preparing to turn on RBAC_ENFORCE_TASK_ROUTES or checking whether legacy acw_ keys and shadow-mode audit results are clean.
---

# RBAC enforce runbook

Read [docs/dev/rbac-enforce-runbook.md](../../../docs/dev/rbac-enforce-runbook.md)
for the exact queries and procedure. Keep steps 1 through 4 in order; all must
be clean before the operator flips `RBAC_ENFORCE_TASK_ROUTES`.

Legacy `acw_` keys without a user or org cannot pass the per-org checks under
enforce. A NULL-user key has no matching org member, so its stdio MCP writes get
403. A NULL-org key also fails the org checks. Before minting an org-scoped key,
every project needs an `org_id`; otherwise its plan sessions are hidden from
org-scoped keys.

1. **Count legacy keys.** Run the runbook's read-only aggregate `api_keys` query.
   It returns `no_user`, `no_org`, `affected`, `affected_used_30d`, and `total`
   counts only. If `affected` is zero, go to step 4.
2. **Reissue keys still in use.** A member of the right org mints a new key bound
   to that user and org. Move clients to the new key through their secret stores,
   then confirm their calls are attributed to the new key and a non-null user in
   the MCP audit trail.
3. **Remove legacy rows.** The operator performs the runbook's SQL delete in one
   transaction, checks the deleted count against step 1 before committing, and
   reruns the aggregate count. `affected` must be zero.
4. **Check shadow-mode audits.** After reissued clients run for at least one normal
   working cycle, run both aggregate audit queries in the runbook: recent shadow
   denials with no user and recent MCP writes by a userless `acw_` key. Both must
   return no rows. Check the pod logs for `rbac.would_deny principal=None` too.

The flag flip and any SQL deletes are the operator's actions. Report only the
aggregate counts; never print key values, IDs, hashes, or owner names.
