---
status: approved
issue: 670
intent: intent/2026-09-18-670-auth-lens-participles.md
---

# Spec: Security and red-team lenses false-block "authenticated" phrasing

## Design

**One pattern, one home.** Keep `_AUTH_RE` in
`apps/backend/plan/review/lenses/security.py`, renamed to `AUTH_RE` (it becomes
a cross-module name). `red_team.py` deletes its copy (`red_team.py:63-68`) and
imports it: `from plan.review.lenses.security import AUTH_RE`. The security
lens is in the mandatory set and is always imported by `default_lenses()`, so
the import adds no registration side effect a red-team run did not already
have.

**The stem.** Replace the leading alternatives
`auth|authn|authz|authentication|authorization` with

    (?:un)?auth(?:n|z|entic\w*|ori[sz]\w*)?

inside the existing `\b( … )\b` group. Everything else in the alternation is
unchanged. Coverage:

| word | matches |
| --- | --- |
| auth, authn, authz | yes |
| authentication, authenticate(s/d), authenticating | yes (`entic\w*`) |
| authorization, authorisation, authorize(d/s), authorised | yes (`ori[sz]\w*`) |
| unauthenticated, unauthorized, unauth | yes (`(?:un)?`) |
| author, authored, authority, authorship, authoritative | **no** |

`authority`/`authoritative` fail because `ori` must be followed by `s`/`z`;
`author`/`authored`/`authorship` fail the trailing `\b`. This is the
false-cover guard from the intent's constraint.

**Decided (intent Q1):** `_NETWORKED_RE` stays duplicated and drifted
(`oauth` only in `security.py`). Unifying it changes which plans red-team sees
as networked — separate behaviour change, not in scope.

## Alternatives rejected

- **`auth\w*`** (the issue's suggestion): matches `author`, `authority`,
  `authored` — a spec that says "authored by the platform team" would count as
  having auth criteria and pass the gate. False-cover is the unrecoverable
  direction.
- **New shared module (`lenses/_patterns.py`)**: an extra file for one
  constant; importing from `security.py` does the same with no new file.
- **Patch `security.py` only**: leaves red-team raising the identical false
  finding (issue comment).

## Risks

- Plans previously flagged "no auth criteria" that wrote only participles now
  pass that check — intended. No plan that passed before can start failing:
  the new pattern is a strict superset of the old auth nouns.
- Any external importer of `security._AUTH_RE` breaks on the rename.
  `git grep _AUTH_RE` shows only the two lenses.

## Verification

- New parametrised test (security + red-team) over: `authenticated`,
  `unauthenticated`, `authenticating`, `authorised`, `unauthorized`, and the
  issue's exact AC — finding NOT raised.
- Negative parametrised cases: a networked plan whose only `auth`-prefixed
  words are `author`, `authored by`, `authority` — finding IS raised.
- Negative control: revert the stem only, the positive cases go red.
- Full existing suites stay green:
  `apps/backend/.venv/bin/pytest tests/test_red_team_lens.py tests/test_plan_service.py tests/test_annotate.py tests/test_approval.py -q`.
