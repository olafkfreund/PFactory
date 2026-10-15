---
status: draft
issue: 864
author: olafkfreund
---

# Intent: the invariant checker finds every package, and every package declares

Implements [RFC-0024](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0024-package-owned-runtime-invariants.md)
§3 (Factory#818). The RFC's decisions are approved; this intent carries them
over and does not reopen them.

## Problem

`scripts/check_invariant_ownership.py:49-57` reads a hand-written `COVERED`
tuple of three packages (`pfactory_secrets`, `factory_invariants`,
`factory_common`). PFactory has 32 top-level packages under `apps/backend`
and `apps/web-server`. The other 29 are invisible to the checker, and so is
any package added later: the gap the pattern exists to close.

## Proposed outcome

- The checker discovers every directory with an `__init__.py` directly under
  `apps/backend` and `apps/web-server`, and fails CI on:
  - a package with no declaration;
  - a declaration naming a package (or module) that no longer exists;
  - a source root with no packages.
- The only hand-written inputs are the source roots and the vendored
  exception map (`factory_common` → sibling `factory_common_invariants.py`),
  and every exception entry must exist on disk.
- `--self-test` still proves the checker can fail, per source root.
- Each of the 29 uncovered packages gets `<pkg>/_invariants.py` that registers
  a real check, or a written reason specific to that package that passes
  `_PLACEHOLDER` and `_EMPTY_REASON_MIN_WORDS`.

## Affected users and systems

PFactory CI (`tests/test_invariant_pilot.py` runs the checker in the main
pytest job). No runtime change: registrations stay inert until something calls
`check_all()`, and `FACTORY_INVARIANTS` still defaults off (that changes in #865).

## Constraints

- Must not edit anything inside `apps/web-server/factory_common/` (vendored
  byte-for-byte; the drift gate rejects extra files).
- Importing a companion must not change runtime behaviour.
- Reasons must not be boilerplate: each says what that package owns.

## Open questions

None. The unit (top-level package), the failure cases and the exception map
are fixed by RFC-0024 §3.
