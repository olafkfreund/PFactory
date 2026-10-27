---
status: approved
issue: 941
intent: intent/2026-10-27-941-audit-export-gate.md
---

# Spec: audit-export org gate

## Design

Copy AIFactory PR #1916.

1. `auth.py` `TokenAuthMiddleware`: the principal dict is the only place a
   service principal can be recognised, and today it is not marked (the
   legacy token is `{"id": "default", "role": "user"}`). Add
   `"is_service": True` to the legacy-token and `DISABLE_AUTH` principals,
   and `api_key_id` / `org_id` to the `acw_` key principal. Keys are only
   added; nothing reads them yet.
2. `routes/audit.py::export_audit_logs`: add `request: Request`. Unless
   `request.state.user["is_service"]`:
   - no `org_id` gives 403;
   - a missing `OrgMember` row, a role below `admin` (`_role_level`), or an
     `acw_` key whose `org_id` differs from the requested one gives 403.
   Update the docstring.

## Alternatives rejected

- Let any member export their own org. Audit logs are admin evidence; the
  per-org `/api/orgs/{org_id}/audit` list route is already admin-only.
- Detect the service principal by `id == "default"`. A human could hold that
  id under DISABLE_AUTH seeding; an explicit flag set only by the middleware
  is clearer and matches AIFactory.
- A `project_authz` module like AIFactory's. This repo has none; one inline
  check is enough.

## Risks

A human who relied on the unscoped export loses it. No code caller exists,
and the service principal keeps it.

## Verification

New `tests/test_audit_export_gate.py`. These fail on dev:
- export by a member (not admin, though the token claims admin) gives 403;
- export by a non-member gives 403;
- unscoped export by a human gives 403 (json and csv);
- an `acw_` key bound to org A gets 403 on org B;
- the real middleware marks the legacy token `is_service`.

These pass before and after: org admin export returns only that org's rows;
service principal scoped and unscoped exports return 200.
