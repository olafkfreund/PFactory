---
status: draft
issue: 800
spec: spec/2026-09-29-800-safety-regex-boundary.md
---

# Plan: four compliance-gate escapes fail on inflected wording

Approved decisions, carried from the spec (quoted, not re-derived):

- Fix **all four** broken escapes in
  `apps/backend/plan/review/lenses/compliance.py`, not only the one #800 reports.
  One token per affected alternative, in the idiom the file already uses
  (`moderat\w+`, `minimi[sz]\w+`).
- The exact patterns, as measured:
  - `_LAWFUL_BASIS_OK_RE` (line 84): `legitimate\s+interest\w*`,
    `purpose\s+limitation\w*`, and `lawful|legal\s+bas[ie]s`
  - `_PROFILING_OK_RE` (92): `automated\s+(?:decision\w*|processing)`
  - `_SAFETY_OK_RE` (95): `block(?:ing)?\s+(?:and\s+report\w*|users?)`
  - `_AGE_OK_RE` (100): `age\s+(?:gat\w+|assurance|verification\w*|check\w*)\b`
- `bas[ie]s` rather than `basis\w*` (irregular plural; "lawful bases" is ordinary
  GDPR wording). `gat\w+` rather than `gate\w*` ("age gating" is *gat* + *ing*).
  `report\w*` stays **inside** the `block…and` branch, not hoisted, so "reporting
  to investors" still does not match.
- One parametrised test table over `(regex, phrase, expected)` covering every
  branch of **all seven** escapes in bare and inflected form, plus negatives per
  regex. The three clean escapes (`_LOCATION_OK_RE`, `_RETENTION_OK_RE`,
  `_ACCOUNT_DELETION_OK_RE`) are included so they stay clean.
- Not touched: the three clean regexes' patterns, the lens's scoring, the
  findings' titles.

Measured already and quoted rather than re-run: the four rejected phrasings fail
on the shipped patterns; the candidate patterns above give **0 mismatches across
45 probes** (31 positives, 14 negatives).

## Steps

1. `apps/backend/plan/review/lenses/compliance.py`: apply the four token changes
   above. Leave the existing `16+` comment in place and add a one-line note on the
   `\b`-before-stem trap that names #800, so the next reader sees why the stems
   carry `\w*`. → verified by step 3.
2. Same file: nothing else changes. Re-read the three clean regexes to confirm
   they are untouched by the edit.
3. `tests/test_compliance_lens.py`: add `test_every_compliance_escape_matches_the_way_people_write_it`
   — `@pytest.mark.parametrize` over a module-level table of
   `(regex_name, phrase, expected)`, resolving the regex via `getattr` on the
   imported `compliance` module. 45 cases, from the spec's probe set. Needs
   `import pytest` and `from plan.review.lenses import compliance` added to the
   existing imports.
4. Run `apps/backend/.venv/bin/pytest tests/test_compliance_lens.py -q` — the new
   table plus the existing suite, all green.
5. Negative control (not committed): revert each of the four tokens **one at a
   time**; the new table must fail for that regex and only that regex, proving
   each token is load-bearing rather than carried by a sibling branch. Restore
   after each.
6. Confirm the direction of the gate has not moved:
   `test_social_spec_raises_every_expected_finding` and
   `test_no_retention_policy_must_not_score_one` must still pass **unchanged** —
   a brief that says nothing is still caught.
7. Commit (the hook runs ruff, the ratchet and the full backend suite), push, open
   the PR against `dev` linking intent, spec and plan.

## Tests

    apps/backend/.venv/bin/pytest tests/test_compliance_lens.py -q
    apps/backend/.venv/bin/pytest tests/ -q -k "compliance or review_lens"

Expected: all pass. The full backend suite runs in the pre-commit hook; the
`backend (ruff + pytest)` and `critical (fast PR gate)` jobs are the CI gates.

## Rollback

Revert the commit. The four escapes return to rejecting inflected wording, and
the four findings go back to blocking briefs that already satisfy them. No state,
no schema, no data — the change is four tokens in four regexes plus a test.
