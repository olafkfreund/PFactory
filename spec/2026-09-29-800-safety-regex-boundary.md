---
status: draft
issue: 800
intent: intent/2026-09-29-800-safety-regex-boundary.md
---

# Spec: four compliance-gate escapes fail on inflected wording

## The defect, precisely

Each `_*_OK_RE` in `apps/backend/plan/review/lenses/compliance.py` wraps its
alternation in `\b(...)\b`. Where an alternative ends in a word **stem**, the
closing `\b` demands a non-word character immediately after it — so the inflected
form cannot match. Four escapes are affected, each guarding a finding that is
`blocking=True` under an enforceable constitution and therefore refuses the plan
at `approve` and at live `emit`:

| Regex | Line | Ends in stem | Rejects |
| --- | --- | --- | --- |
| `_LAWFUL_BASIS_OK_RE` | 84 | `legitimate\s+interest` | `legitimate interests` |
| `_PROFILING_OK_RE` | 92 | `automated\s+decision` | `automated decisions` |
| `_SAFETY_OK_RE` | 95 | `block…\s+and\s+report` | `blocking and reporting` |
| `_AGE_OK_RE` | 100 | `age\s+…\s+gate\b` | `age gates`, `age gating` |

## Design

One token per affected alternative, following the idiom already in the file
(`moderat\w+`, `minimi[sz]\w+`). Measured patterns, all four:

    _LAWFUL_BASIS_OK_RE
      r"(?i)\b(lawful\s+bas[ie]s|legal\s+bas[ie]s|purpose\s+limitation\w*|"
      r"legitimate\s+interest\w*)\b"

    _PROFILING_OK_RE
      r"(?i)\b(profiling|automated\s+(?:decision\w*|processing)|"
      r"art(?:icle)?\.?\s*22)\b"

    _SAFETY_OK_RE
      r"(?i)\b(block(?:ing)?\s+(?:and\s+report\w*|users?)|report(?:ing)?\s+"
      r"(?:abuse|users?|content)|moderat\w+|notice[\s-]and[\s-]action)\b"

    _AGE_OK_RE
      r"(?i)\b(?:age\s+(?:gat\w+|assurance|verification\w*|check\w*)\b|minimum\s+age\b|"
      r"under[\s-]?1[38]\b|1[368]\s*\+|coppa\b|age[\s-]appropriate\b|"
      r"parental\s+consent\b)"

Three details worth stating, each found by measuring rather than by reading:

- **`bas[ie]s`, not `basis\w*`.** The plural of *basis* is irregular, and "lawful
  bases" is ordinary GDPR wording that `\w*` would still reject.
- **`gat\w+`, not `gate\w*`.** "age gating" is *gat* + *ing*, so a pattern anchored
  on the full word `gate` misses it. This is the one candidate that failed my
  first pass.
- **`report\w*` stays inside the `block…and` branch.** It is not hoisted, so
  "reporting to investors" still does not match — verified as a negative.

### The test that should have caught this

`tests/test_compliance_lens.py` asserts the findings *fire* and nothing pins the
escapes, which is why all four survived. Add one parametrised table over
`(regex, phrase, expected)` covering **every branch of all seven escapes** in bare
and inflected form, plus negatives per regex. 45 cases as measured below.

The table is the real deliverable. This is the **fourth** boundary defect in this
file — `#397` for "untrusted", the `16+` comment above `_AGE_OK_RE` (on a regex
still broken for `age gates`), and now these. A warning comment has not stopped
the trap recurring; an executable table will.

## Alternatives rejected

- **`report(?:ing)?` only, as #800 proposes.** It patches the wrong alternative —
  the `report(?:ing)?\s+(?:abuse|users?|content)` branch already works — so the
  reported false block would survive the fix.
- **Drop the trailing `\b` entirely.** Cheapest, but it lets a stem match inside
  an unrelated word, which is how loose matching produces false *passes* on a
  hard gate. Widening is the failure mode to avoid here.
- **Fix only `_SAFETY_OK_RE` and file the other three.** Same one-token defect,
  same file, same test; splitting leaves three known false blocks in place,
  including the GDPR term of art.
- **Replace the regexes with an LLM judgement.** Out of proportion, and it makes
  a hard gate non-deterministic.

## Risks

- **Over-widening a hard gate.** `\w*` on a stem admits nonsense inflections
  ("legitimate interesting"), which is harmless, but it must not admit a brief
  that says nothing. Mitigated by 14 negative probes, all still failing.
- `_LOCATION_OK_RE`, `_RETENTION_OK_RE` and `_ACCOUNT_DELETION_OK_RE` measure
  clean and are **not** touched. They go in the test table so they stay that way.
- The compliance lens feeds scores; loosening an escape lowers findings on some
  briefs. `test_social_spec_raises_every_expected_finding` and
  `test_no_retention_policy_must_not_score_one` pin that direction and must stay
  green unchanged.

## Verification

- **Reproduction first:** on the shipped patterns, the four rejected phrasings in
  the table above fail to match. Already measured; re-run as the before half.
- **After:** 45 probes — 31 positives (each branch bare and inflected) and 14
  negatives — all as expected. Measured on the candidate patterns above:
  `mismatches: 0`.
- **Negative control:** revert each of the four tokens one at a time; the new
  table must fail for that regex and only that regex. Proves each token is
  load-bearing rather than carried by a sibling branch.
- **Unchanged suites:** `tests/test_compliance_lens.py` in full, plus the full
  backend suite via the pre-commit hook.
