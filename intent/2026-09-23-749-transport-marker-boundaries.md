---
status: draft
issue: 749
author: Olaf Krasicki-Freund
---

# Intent: A vanished digest can be skipped as "registry unavailable"

## Problem

#744 / #748 split registry failures into two categories: the registry never
answered (skip — the environment cannot answer, like docker being absent) and
the registry answered with a refusal (fail — a pinned digest that vanished is a
real defect). The split is right; the classifier implementing it is not.

`_TRANSPORT_MARKERS` (`tests/docker/helpers.py`) matches the bare substrings
`"502"`, `"503"`, `"504"`, `"429"` anywhere in stderr. A refusal echoes the
full ref, digest included — measured against a real registry:

    ERROR: docker.io/library/alpine@sha256:503edd78…: not found

Fed to the merged helper, that classifies as a transport error, so
`test_multi_arch_buildable` retries and then **skips**. The multi-arch gate
passes without checking, for exactly the defect the `ManifestInspectError`
branch exists to catch.

Measured:

- `_is_transport_error("…@sha256:503edd78…: not found")` → `True` (skip);
- the same string with a digest free of marker digits → `False` (fails, as
  intended);
- a random sha256 contains one of the four sequences ~5.9% of the time
  (200k-sample simulation). The three digests pinned on `dev` today are clean,
  so this is latent, not currently firing.

The shipped tests cannot catch it: their refusal cases are bare strings
(`"manifest unknown"`, `"unauthorized: authentication required"`) with no ref,
so a digest never reaches the classifier.

## Proposed outcome

- A refusal is classified as a refusal — and fails — whatever digits the digest
  happens to contain.
- A genuine HTTP 429/502/503/504 from the registry still counts as "never
  answered" and still skips.
- A test pins the case, so the guard cannot regress silently: a refusal whose
  digest carries a marker sequence must FAIL, not skip.

## Affected users and systems

- `tests/docker/helpers.py` (the classifier), `tests/test_registry_inspect_helper.py`
  (its unit tests), `tests/docker/test_p0_multi_arch.py` (a stale comment
  naming `ManifestInspectFailed`; the class is `ManifestInspectError`).
- The required `docker (P0 acceptance)` and `backend (ruff + pytest)` checks,
  which both run the multi-arch test.

## Constraints

- Keep #748's behaviour in every other respect: same two categories, same
  retry-once-with-backoff, same skip reason wording.
- Precision in one direction only: misclassifying a transport error as a
  refusal costs a red CI run that a re-run clears; misclassifying a refusal as
  a transport error hides a real defect behind a green gate. Prefer the former.
- No new dependency; this is a matching change in a test helper.

## Open questions

None.
