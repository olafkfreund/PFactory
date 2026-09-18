---
status: approved
issue: 682
spec: spec/2026-09-18-682-registry-mandatory-lenses.md
---

# Plan: The registry shows a toggle on a lens it will never switch off

Approved decisions (from the spec):

- `extension-registry.json` (PFactory `apps/backend/plan/emit/contracts/` and
  Factory hub `apis/`, kept byte-identical): `compliance-review` gains
  `"mandatory": true`, keeps `"enabled": true` (hub `enabled_only()` compat);
  its description and the top-level description each gain one sentence
  (mandatory entries always run; `enabled` gates optional extensions only).
- PFactory `plan/review/lenses/base.py`: `default_lenses()` docstring states
  the asymmetry; new `_warn_if_mandatory_disabled(order)` logs one WARNING per
  entry per process when a mandatory lens's `<lens>-review` entry has raw
  `enabled` False or lacks `mandatory: true`. Never raises, never changes the
  returned list.
- PFactory tests: mandatory entries carry `mandatory: true` + `enabled: true`
  (non-vacuous); a disabled mandatory entry still runs and warns.
- Hub `scripts/extension_registry.py`: `validate_entry` errors on
  `mandatory: true` without `enabled is True`; self-check gains that case.
- No entries added for the other five mandatory lenses. No change to which
  lenses run.

## Steps

PFactory (branch `fix/682-registry-mandatory-lenses`):

1. Registry JSON: the three edits above, preserving key order and 2-space
   formatting. → verify `python -m json.tool` parses it.
2. `base.py`: docstring + `_warn_if_mandatory_disabled`, called in
   `default_lenses()` after `order` is built. → verify existing lens tests pass.
3. `tests/test_lens_gating.py`: add
   - `test_mandatory_lens_entries_are_marked_and_enabled` (reads the vendored
     JSON; asserts the scan found `compliance-review`);
   - `test_disabled_mandatory_entry_still_runs_and_warns` (temp registry via
     `PFACTORY_EXTENSION_REGISTRY` + `reset_cache()`, `caplog`).
4. Negative control (not committed): set `compliance-review` to
   `enabled: false` in the vendored JSON → the first test fails; restore.

Factory hub (new worktree from `origin/main`, branch
`fix/pfactory-682-registry-mandatory`; the existing checkout on
`fix/1712-kotlin-descriptor` is not touched):

5. Copy PFactory's edited JSON over `apis/extension-registry.json`
   → verify `diff` of the two files is empty.
6. `scripts/extension_registry.py`: the new `validate_entry` rule + a
   `_test_validate_rejects` case (`mandatory: true, enabled: false` rejected;
   `mandatory: true, enabled: true` accepted). → verify
   `python scripts/extension_registry.py` prints the pass line.
7. Negative control (not committed): with the hub entry set to
   `enabled: false`, the self-check's `_test_real_registry` fails; restore.

PRs:

8. PFactory PR to `dev` (links the hub PR); hub PR to `main` (links the
   PFactory PR and the three artifact files).

## Tests

    apps/backend/.venv/bin/pytest tests/test_lens_gating.py tests/test_red_team_lens.py tests/test_compliance_lens.py -q
    (cd <factory-worktree> && python scripts/extension_registry.py)
    diff apps/backend/plan/emit/contracts/extension-registry.json <factory-worktree>/apis/extension-registry.json

Expected: tests pass, self-check passes, diff empty.

## Rollback

Revert each PR. The added `mandatory` key is ignored by every consumer, so a
partial rollback (one repo only) is harmless apart from the copies differing.
