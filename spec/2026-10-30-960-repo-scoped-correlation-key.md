---
status: approved
issue: 960
intent: intent/2026-10-30-960-repo-scoped-correlation-key.md
---

# Spec: never emit a bare issue number as the correlation key

## Design

`origin_key(repo, n)` takes a required repo. `correlation_key_for`: an origin
issue with `session.repo` gives `<repo>#<n>`; an origin issue without it gives
`pf-<session_id>` (it does not fall through to the epic: keying on PFactory's
own output loses the link to the request, per the #874 precedence). An epic
with `emit_repo` gives `<emit_repo>#<n>`, else `pf-<session_id>`.

## Alternatives rejected

- Derive the repo from the project's configured repo: a session's origin repo
  is per-request; a guess can misattribute the work item.
- Fall through from a repo-less origin to the epic: re-points the chain at
  PFactory's output (#874).
- Re-key sessions that already persisted a bare key: their earlier events used
  it; changing it mid-life splits the row (pinned by an existing test).

## Risks

Sessions created after this change with no repo lose cross-service joins they
did not have anyway after CFactory #506.

## Verification

`tests/test_plan_completion.py` and `tests/test_plan_from_issue_route.py`:
missing repo yields `pf-<session_id>` for origin and epic; repo cases unchanged.
