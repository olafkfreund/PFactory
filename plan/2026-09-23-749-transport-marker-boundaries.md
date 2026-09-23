---
status: approved
issue: 749
spec: spec/2026-09-23-749-transport-marker-boundaries.md
---

# Plan: A vanished digest can be skipped as "registry unavailable"

Approved decisions (from the spec):

- Phrase markers stay plain substrings (none can occur inside a hex digest:
  each has a space, slash or non-hex letter). The four digit-only HTTP codes
  move to `_TRANSPORT_CODE_RE = re.compile(r"\b(?:429|502|503|504)\b")`.
- `_is_transport_error` = any phrase marker **or** a `_TRANSPORT_CODE_RE`
  search. Exception types, retry-once-with-backoff and skip wording unchanged.
- Only one behaviour changes: a refusal whose digest contains 429/502/503/504
  fails instead of skipping.
- `tests/docker/test_p0_multi_arch.py`: comment says `ManifestInspectError`,
  not `ManifestInspectFailed`.

## Steps

1. `tests/docker/helpers.py`: `import re`; drop `"503"`, `"502"`, `"504"`,
   `"429"` from `_TRANSPORT_MARKERS`; add `_TRANSPORT_CODE_RE` beside it with a
   comment naming the digest collision (#749); `_is_transport_error` checks
   both. → verify by step 3's new test and the existing ones.
2. `tests/docker/test_p0_multi_arch.py`: fix the class name in the comment.
   → verify `git grep -n ManifestInspectFailed` is empty.
3. `tests/test_registry_inspect_helper.py`: add to the refusal parametrise a
   case carrying a marker-bearing digest, e.g.
   `"ERROR: docker.io/library/alpine@sha256:503edd78…c6: not found"` — the
   existing assertions then require `ManifestInspectError`, one call, no sleep.
   Add a one-line comment saying why the digest matters (it is the #749 case).
   → verify the file's tests pass.
4. Negative control (not committed): restore the four bare substrings in
   `_TRANSPORT_MARKERS` → the new refusal case fails (skips instead); the
   transport cases stay green. Restore.
5. Confirm real codes still skip: the existing transport parametrise already
   covers `503 Service Unavailable` and `toomanyrequests: 429`; both must stay
   green in step 3's run (no separate assertion needed).

## Tests

    apps/backend/.venv/bin/pytest tests/test_registry_inspect_helper.py -q
    apps/backend/.venv/bin/pytest tests/docker -m "not docker" -q
    apps/backend/.venv/bin/pytest tests/ -q -k "registry or multi_arch or docker"

Expected: all pass. Full backend suite runs in the pre-commit hook.

## Rollback

Revert the commit; the classifier returns to substring matching (a refusal with
marker digits skips again). Test-only change, no runtime code, no state.
