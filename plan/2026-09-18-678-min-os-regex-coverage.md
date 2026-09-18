---
status: draft
issue: 678
spec: spec/2026-09-18-678-min-os-regex-coverage.md
---

# Plan: OS-floor phrasing is not recognised as covering min-os-versions

Approved decisions (from the spec):

- A keyword may be a compiled `re.Pattern`; `missing_requirements` uses
  `pattern.search(hay)` for those and `kw in hay` for strings. Tuple shape
  `(key, text, keywords)` unchanged; only this matcher reads keywords.
- `min-os-versions` gains two patterns over the lower-cased haystack:
  `\b{OS}\s*{VER}{AFTER}` and
  `\b(?:at\s+least|minimum(?:\s+of)?|min\.?)\s+{OS}\s*{VER}\b`, with
  `OS=(?:ios|ipados|android)`, `VER=\d+(?:\.\d+)*`,
  `AFTER=(?:\s*\+|\s+(?:and|or)\s+(?:above|up|later|newer|higher))`.
- Prototype-verified: 8 floor phrasings covered, 6 traps (incl. "supports iOS
  and Android", "tested on iOS 17", "radios 5 and up", "audios 3+") not.

## Steps

1. `apps/backend/plan/decompose/implicit_requirements.py`:
   a. `import re`; add `Requirement = tuple[str, str, tuple[str | re.Pattern[str], ...]]`
      and use it in the five places that spell the tuple type (lines 43, 73,
      204, 250, 271);
   b. module-level `_OS`, `_VER`, `_AFTER` and the two compiled patterns,
      appended to the `min-os-versions` keywords tuple;
   c. `missing_requirements`: coverage test handles `re.Pattern`;
   d. update the comment above `SERVICE_IMPLICIT_REQUIREMENTS` (keywords may
      be regexes, matched on the lower-cased haystack).
   → verify existing `tests/test_implicit_requirements.py` passes except the
   one assertion step 2 flips.
2. `tests/test_implicit_requirements.py`:
   - `test_min_os_and_forced_upgrade_overlap_phrasing`: flip to
     `"min-os-versions" not in missing`, update its comment;
   - new `test_os_floor_phrasings_cover_min_os` (parametrised, 8 phrasings);
   - new `test_platform_mentions_without_a_floor_do_not_cover_min_os`
     (parametrised, 6 traps).
3. Negative controls (not committed): (a) remove the two patterns → the 8
   floor cases fail; (b) drop the leading `\b` from both → "radios 5 and up"
   and "audios 3+" fail. Restore.
4. Related suites: `tests/test_readiness_checks.py`, completeness-lens tests,
   `tests/test_plan_service.py`.

## Tests

    apps/backend/.venv/bin/pytest tests/test_implicit_requirements.py tests/test_readiness_checks.py tests/test_plan_service.py -q
    apps/backend/.venv/bin/pytest tests -q -k "completeness or implicit or mobile"

Expected: all pass. Full suite via the pre-commit hook.

## Rollback

Revert the commit; min-os returns to substring-only matching (duplicate AC for
natural phrasings). No state involved.
