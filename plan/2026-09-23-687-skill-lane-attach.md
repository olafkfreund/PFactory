---
status: draft
issue: 687
spec: spec/2026-09-23-687-skill-lane-attach.md
---

# Plan: The registry's skill lane is declared but nothing reads it

Approved decisions (from the spec):

- New `apps/backend/plan/emit/skills_block.py` with `derive_needs(contract,
  plan)`, `build_skills_block(contract, plan)` and `attach_skills(contract,
  plan)`; never raises.
- Needs table: obligations non-empty → `compliance-block`; data classes ∩
  {account, location, personal-profile, profile, profiling, user-contact} →
  `privacy`; `plan.plan_type == "mobile-app"` → `mobile`. `store-distribution`
  is deliberately unmapped.
- A skill matches when `enabled` and `capabilities` intersect the needs.
- Block at `epic_context["skills"]`: `available`, `source`
  (`pfactory:plan-registry`), `matched_on` (sorted needs), `skills[]` of
  `{id, title, path}`. Absent key / `available: false` / `available: true` with
  an empty list are three distinct states.
- Called in `contract_emit.py` immediately after `attach_compliance`.

Addition agreed while writing this plan: the schema declares the new block the
way `house_standards` / `constitution` / `compliance` do — `$defs.skills` plus
an `epic_context.properties.skills` `$ref`. `epic_context` is already an open
object, so this is documentation, not enforcement; the repo has a test per
block asserting the `$def` exists.

## Steps

1. `apps/backend/plan/emit/skills_block.py`: the three functions plus the
   needs table as a module constant. Reads the lane via
   `load_registry().enabled("skill")`. → verify by step 4's unit tests.
2. `apps/backend/plan/emit/contract_emit.py`: import and call
   `attach_skills(contract, plan)` right after `attach_compliance(...)`, with
   a comment naming #687 and why the order matters (needs read the compliance
   block). → verify by step 5's integration tests.
3. `apps/backend/plan/emit/contracts/task-contract.schema.json`: add
   `$defs.skills` and the `epic_context.properties.skills` `$ref`, additive.
   → verify `validate_contract(contract) == []` in step 5.
4. `tests/test_skills_block.py` (new), offline:
   - `derive_needs` per table row: obligations → `compliance-block`; each
     personal data class → `privacy`; `mobile-app` → `mobile`;
   - `store-distribution` alone derives **no** need (the guard);
   - matching against a fake registry: disabled row excluded, empty
     capabilities excluded, no intersection excluded, intersection included;
   - `attach_skills` on `{}` with a `None` plan returns the contract unchanged
     and does not raise;
   - an unreadable catalogue (monkeypatched loader raising) → `available:
     false`, `skills: []`, no exception.
5. `tests/test_skills_contract.py` (new), mirroring
   `tests/test_compliance_contract.py`: `assemble_contract` on
   - a mobile-app plan → `epic_context.skills.skills` contains
     `skill:mobile-native`;
   - a personal-data plan whose compliance lens raises obligations →
     contains `skill:privacy-and-regulatory`;
   - a plain software plan → `available: true`, `skills: []`,
     `matched_on: []`;
   - every case: `validate_contract(contract) == []`;
   - the schema declares `$defs.skills`.
6. Negative control (not committed): comment out the `attach_skills(...)` call
   → step 5's tests fail, and the assertion that distinguishes absent from
   empty is the one that catches it. Restore.
7. Second control (not committed): map `store-distribution` → `store-review`
   in the table → the guard test in step 4 fails (a web plan mentioning app
   stores would pull in the mobile skill). Restore.

## Tests

    .venv/bin/pytest tests/test_skills_block.py tests/test_skills_contract.py -q
    .venv/bin/pytest tests/test_registry_skill_entries.py tests/test_compliance_contract.py \
      tests/test_contract_emit.py tests/test_contract_builder.py tests/test_contract_handshake.py -q
    .venv/bin/pytest tests/ -q -k "contract or registry or skill"

(The worktree has no venv; use the main checkout's
`/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest`.)

Expected: all pass. Full backend suite runs in the pre-commit hook.

## Rollback

Revert the commit. The block is additive and `epic_context` is an open object,
so a contract emitted with it stays valid against the reverted schema; no
consumer requires the key.
