---
status: approved
issue: 670
spec: spec/2026-09-18-670-auth-lens-participles.md
---

# Plan: Security and red-team lenses false-block "authenticated" phrasing

Approved decisions (from the spec):

- One auth pattern, `AUTH_RE`, lives in `plan/review/lenses/security.py`;
  `red_team.py` imports it and drops its copy.
- The leading auth alternatives become `(?:un)?auth(?:n|z|entic\w*|ori[sz]\w*)?`
  inside the existing `\b( … )\b`; the rest of the alternation is unchanged.
- Must match: auth, authn, authz, authentication, authenticated,
  authenticating, authorization, authorisation, authorized, authorised,
  unauthenticated, unauthorized. Must NOT match: author, authored, authority,
  authoritative, authorship (false-cover guard).
- `_NETWORKED_RE` is left alone (drift stays; out of scope).
- No score, threshold, or finding-text change.

## Steps

1. `apps/backend/plan/review/lenses/security.py`: rename `_AUTH_RE` → `AUTH_RE`,
   replace the leading alternatives with the stem; update the one use site
   (`security.py:119`). → verify by `python -c` import + the table above.
2. `apps/backend/plan/review/lenses/red_team.py`: delete `_AUTH_RE`
   (lines 63-68), add `from plan.review.lenses.security import AUTH_RE`, update
   the use site (`_security_scope`). → verify by `git grep -n "_AUTH_RE"` empty.
3. `tests/test_auth_lens_participles.py` (new): parametrised over both lenses
   (red-team enabled via the same monkeypatch as `tests/test_red_team_lens.py`):
   - networked software plan whose only auth statement uses each participle,
     plus the issue's exact AC → auth finding absent;
   - networked plan whose only `auth…` words are author/authored/authority →
     auth finding present;
   - a direct table test of `AUTH_RE` against the must/must-not lists.
   → verify by running it green.
4. Negative control: temporarily restore the old noun alternation, re-run
   step 3's file, confirm the participle cases fail; restore. (Not committed.)

## Tests

    apps/backend/.venv/bin/pytest tests/test_auth_lens_participles.py tests/test_red_team_lens.py \
      tests/test_plan_service.py tests/test_annotate.py tests/test_approval.py -q

Expected: all pass.

## Rollback

Revert the commit; both lenses return to their own identical noun-only
pattern. No data or state involved.
