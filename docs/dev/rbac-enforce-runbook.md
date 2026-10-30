# Runbook: turning on `RBAC_ENFORCE_TASK_ROUTES` (S6)

Slice S6 of the fleet RBAC epic (Factory#312) flips `RBAC_ENFORCE_TASK_ROUTES` from
shadow mode (off, the default) to enforce. In shadow mode, every project, task and
plan check still runs. A denial is only logged (`rbac.would_deny`) and audited, and
the response does not change. Under enforce, the same denial is a 403 (or a 404 for
plan sessions).

This page covers one pre-flip check: **legacy `acw_` keys with no `user_id`**
(#959). Other S6 work, such as giving every project an `org_id`, is tracked in
Factory#312.

## Why legacy keys break under enforce

From #957 on, the stdio MCP write proxies (`apps/web-server/server/mcp_stdio/router.py`)
authorize an `acw_` key as its own principal:

```python
{"id": key.user_id, "api_key_id": key.key_id, "org_id": key.org_id}
```

A key minted before keys were bound to a user has `user_id` NULL. Its principal is
not the service principal, and no `OrgMember` row can match a NULL user. Once
enforce is on, **every MCP write made with such a key gets 403**:

- task `start`, `stop`, `recover`, `approve-plan`, `create-pr`, `merge`, `create-and-run`;
- plan `ingest`, `process`, `approve`.

A key with `org_id` NULL fails the same way: `PlanAccessChecker` finds no role in a
NULL org, and the project checks see a key "scoped to another organization".

Two more things about these keys:

- `mcp_remote/auth.py` caps a key's scopes by its owner's current org role only when
  `user_id` is set. A NULL-user key keeps every scope it was minted with, and an
  owner's demotion never reaches it. Reissuing it closes that gap too.
- `DELETE /api/keys/{id}` only revokes keys whose `user_id` is the caller. A NULL-user
  key cannot be revoked through the API. Step 3 below removes it in SQL.

**Not affected:** callers that use the legacy admin token (`APP_API_TOKEN`). They
stay the service principal (`is_service`) and bypass the per-org checks.

> On a database built by the alembic baseline, `api_keys.user_id` and
> `api_keys.org_id` are `NOT NULL`, so step 1 should return zero. Run it anyway: a
> database created before the baseline, or restored from an older dump, can still
> hold such rows. AIFactory #1914 also found 0 rows in `api_keys` at that time.

## Before minting the first org-scoped key

Give the projects an `org_id` first. From #968 on, an org-scoped `acw_` key sees a
plan session only when the session's project is in the key's org. Sessions whose
project has no `org_id`, or whose repo matches no project, are hidden from every org
key, even in shadow mode. A key minted before the projects have an org therefore sees
no plan sessions.

**Before enforce, every project needs an `org_id`, for humans as well as keys.** Under
enforce, a plan session whose project has no `org_id` (or whose repo matches no
project) is hidden from every non-service caller, people included (#968).

## Order of operations

Do these in order. Do not flip the flag until steps 1 to 4 are clean.

### 1. Count the legacy keys (aggregate only)

Run read-only against the PFactory database. These queries return counts, never key
ids, names, hashes or owners, so their output is safe to paste into an issue or PR.

```sql
-- How many keys would 403 on every MCP write under enforce.
SELECT
  count(*) FILTER (WHERE user_id IS NULL)                     AS no_user,
  count(*) FILTER (WHERE org_id  IS NULL)                     AS no_org,
  count(*) FILTER (WHERE user_id IS NULL OR org_id IS NULL)   AS affected,
  count(*) FILTER (WHERE (user_id IS NULL OR org_id IS NULL)
                     AND last_used_at > now() - interval '30 days') AS affected_used_30d,
  count(*)                                                    AS total
FROM api_keys;
```

If `affected` is 0, skip to step 4. `affected_used_30d` tells you how many of them
are still in use: those need a reissue (step 2), and the rest can simply be removed
(step 3).

### 2. Reissue the keys that are still in use

A key cannot be re-bound in place: its owner and org are fixed when it is minted. For
each client that still uses a legacy key:

1. A human who is a member of the right org signs in to the portal and mints a new
   key with `POST /api/keys` (Settings → API keys), with `org_id` set to that org. The
   new key is bound to that user and org. Write scopes (`task:write`, `plan:write`,
   `task:merge`) need the `member` role. A viewer can mint only `mcp:read`.
2. Put the new key in the client's secret store (the client's agenix or Kubernetes
   secret, never a plaintext file or a chat message) and roll the client.
3. Confirm that the client's calls show up under the new key: its `mcp.*` audit rows
   carry the new key's id in `details_json.mcp_key_id`, and a non-null `user_id`.

Do not paste key values, key ids or owner names into issues or PRs. Report only the
counts from step 1 and step 4.

### 3. Remove the legacy rows

When the clients are moved over, delete the legacy rows in one transaction, and check
the count before you commit:

```sql
BEGIN;
DELETE FROM api_keys WHERE user_id IS NULL OR org_id IS NULL;
-- psql prints "DELETE <n>": n must equal `affected` from step 1. If not, ROLLBACK.
COMMIT;
```

Re-run the step 1 query. `affected` must be 0.

### 4. Check the shadow-mode audit trail

While the flag is still off, the shadow denials that a legacy key causes are audited
with a NULL `user_id`: the key's principal id is its NULL `user_id`. The legacy admin
token never reaches this path, because it is the service principal. Let the
reissued clients run for at least one normal working cycle, then run:

```sql
-- Shadow denials with no user behind them, last 7 days. Must be 0 before the flip.
SELECT action, count(*)
FROM audit_logs
WHERE action IN ('authz.denied', 'gate.rejected')
  AND details_json LIKE '%"shadow": true%'
  AND user_id IS NULL
  AND created_at > now() - interval '7 days'
GROUP BY action;

-- MCP writes made by an acw_ key with no user, last 7 days. Must be 0.
SELECT action, count(*)
FROM audit_logs
WHERE action LIKE 'mcp.%'
  AND user_id IS NULL
  AND details_json NOT LIKE '%"mcp_key_id": "legacy-admin"%'
  AND created_at > now() - interval '7 days'
GROUP BY action;
```

Both must return no rows. The pod logs give the same signal: search for
`rbac.would_deny principal=None`.

### 5. Flip the flag

Set `APP_RBAC_ENFORCE_TASK_ROUTES=true` for PFactory in `factory-gitops`. The setting is
`RBAC_ENFORCE_TASK_ROUTES` in `server/config.py`, and its env var carries the `APP_` prefix. This is an
owner-approved change, because it affects logins. Watch the pod logs and `authz.denied`
audit rows for the first hour.

**Rollback:** set `APP_RBAC_ENFORCE_TASK_ROUTES=false` in `factory-gitops` and let the pods
roll. Shadow mode resumes at once. No data changes are involved.
