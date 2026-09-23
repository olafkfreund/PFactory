---
status: draft
issue: 687
intent: intent/2026-09-23-687-skill-lane-attach.md
---

# Spec: The registry's skill lane is declared but nothing reads it

## Facts established

- Skill rows carry `id`, `title`, `capabilities[]`, `enabled`, `config.path`
  (`plan/registry/catalogue/catalogue.yaml`); `load_registry()` returns them
  and `Registry.enabled("skill")` already filters the lane.
- `attach_compliance` writes the block to **`contract["compliance"]`**
  (top level, not `epic_context`), and runs before the end of assembly
  (`contract_emit.py:188`). So a skills step placed after it can read it.
- The compliance lens's data-class vocabulary is closed:
  `account`, `location`, `personal-profile`, `profile`, `profiling`,
  `store-distribution`, `user-contact`.
- Plan types are files under `plan/plan_types/`; `plan.plan_type` is a plain
  string (`mobile-app`, `software-service`, …).

## Design

New module `apps/backend/plan/emit/skills_block.py`, mirroring
`constitution.py` / `compliance_block.py`.

### Deriving the contract's needs (intent Q1)

One table-driven function, `derive_needs(contract, plan) -> set[str]`:

| signal at assembly time | need |
| --- | --- |
| `contract["compliance"]["obligations"]` non-empty | `compliance-block` |
| `contract["compliance"]["data_classes"]` ∩ {account, location, personal-profile, profile, profiling, user-contact} | `privacy` |
| `plan.plan_type == "mobile-app"` | `mobile` |

Deliberately **not** mapped: `store-distribution`. It is a data class about app
stores that a non-mobile plan can mention, and mapping it to `store-review`
would pull the mobile skill into a web plan — the wrong direction per the
intent's constraint. The mobile skill is reached by plan type alone.

Adding a signal later is one row plus one test, not a rewrite.

### Matching

A skill row matches when it is `enabled` and its `capabilities` intersect the
derived needs. With today's rows: an obligations-bearing or personal-data plan
matches `skill:privacy-and-regulatory`; a `mobile-app` plan matches
`skill:mobile-native`. A plan with neither matches nothing.

### The block

`attach_skills(contract, plan)` writes `epic_context["skills"]`:

    {
      "available": true,                      # the catalogue was read
      "source": "pfactory:plan-registry",
      "matched_on": ["compliance-block", "privacy"],   # sorted needs
      "skills": [{"id": …, "title": …, "path": "skills/engineering/…md"}]
    }

Three distinguishable states, as the intent requires:

- key absent → the feature did not run (contract predates it);
- `available: false`, `skills: []` → the catalogue could not be read;
- `available: true`, `skills: []` → it ran and matched nothing (`matched_on`
  shows what was looked for).

Per skill: `id`, `title`, `path` (intent Q2 — the path is openable by a coder
with the repo; a URL would need a base the contract does not carry).

`attach_skills` never raises (bare `except` → return contract unchanged), and
is called in `contract_emit.py` immediately after `attach_compliance`, so the
compliance-derived needs are visible.

## Alternatives rejected

- **Match on free text** (scan the plan for "gdpr", "ios"): the false-positive
  direction the intent forbids; the registry already declares capabilities.
- **Attach every enabled skill**: makes the lane load-bearing but tells the
  coder nothing — the point is *which* skill this contract needs.
- **Put the block at the contract top level** beside `compliance`: the intent
  places it with the other coder-facing context (`house_standards`,
  `constitution`), which lives in `epic_context`.
- **Serve URLs instead of paths**: needs a base URL the contract lacks.
- **Derive needs from tfactory lanes / target kind**: no skill row today keys
  on them; add as table rows when a row does.

## Risks

- A new skill row with a broad capability (e.g. `release`) would start matching
  widely. Mitigated by matching only against derived needs — `release` is not a
  need today, so such a row matches nothing until a signal maps to it.
- `epic_context` grows by one small block; AIFactory ignores unknown keys
  (same contract as `constitution`).

## Verification

- Unit: `derive_needs` for each table row and for the store-distribution
  non-mapping; matching with a fake registry (enabled/disabled rows, empty
  capabilities, no intersection).
- Integration: a contract built from a mobile plan carries
  `skill:mobile-native`; one from a personal-data plan with obligations carries
  `skill:privacy-and-regulatory`; a plain software plan carries
  `available: true, skills: []` — the "ran and matched nothing" state.
- Negative control: unwire the `attach_skills(...)` call in `contract_emit.py`
  → the integration tests go red (and the block is absent, not empty).
- Robustness: an unreadable catalogue yields `available: false` and never
  raises; `emit_contract` still succeeds.
- Existing suites green: `tests/test_registry_skill_entries.py`,
  `tests/test_contract_emit*.py`, compliance/constitution attach tests.
