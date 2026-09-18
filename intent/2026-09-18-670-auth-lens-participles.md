---
status: draft
issue: 670
author: Olaf Krasicki-Freund
---

# Intent: Security and red-team lenses false-block "authenticated" phrasing

## Problem

The security lens decides whether a plan covers auth with `_AUTH_RE`
(`apps/backend/plan/review/lenses/security.py:32`). The pattern lists nouns
(`authentication`, `authorization`) and a bare `auth` bounded by `\b`, so the
participles `authenticated` / `unauthenticated` never match. A plan whose AC
reads "exposes … over an authenticated API, and rejects any unauthenticated
call" is told it has no auth criteria, the lens caps at 0.7 (< 0.75 threshold)
and the gate fails. Measured on session `010-myfriends`, pfactory 0.6.16.

The same pattern is copied byte-for-byte into
`apps/backend/plan/review/lenses/red_team.py:63`, which raises the identical
false finding. Fixing one copy leaves the symptom in the other.

## Proposed outcome

- A plan that states auth with any `auth*` word form (auth, authn, authz,
  authentication, authorization, authenticated, authenticating,
  unauthenticated) no longer gets the "no auth criteria" finding from either
  lens, and no longer fails the gate for that reason.
- The auth keyword pattern exists in one place; both lenses use it.
- A parametrised test over the participle forms pins it, for both lenses.

## Affected users and systems

- Every plan run through review gates (all tenants) — security lens always,
  red-team lens when enabled.
- `apps/backend/plan/review/lenses/security.py`, `red_team.py`, tests.

## Constraints

- Must not loosen the check into a false-cover: words that merely start with
  `auth` but are not about access control (`author`, `authored`, `authority`)
  must NOT count as auth coverage — that direction silently passes a plan.
- No change to lens scores/thresholds or finding text.

## Open questions

1. `_NETWORKED_RE` is also duplicated but has already drifted: `security.py`
   includes `oauth`, `red_team.py` does not. Unify it in the same change
   (pick one list), or leave it and file separately? Recommendation: leave it —
   unifying changes which plans red-team considers "networked", a behaviour
   change outside this issue.
