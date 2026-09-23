---
status: draft
issue: 687
author: Olaf Krasicki-Freund
---

# Intent: The registry's skill lane is declared but nothing reads it

## Problem

The plan registry has carried `kind: skill` since it existed, and the lane now
holds two rows (`catalogue.yaml`):

    - id: skill:privacy-and-regulatory
      capabilities: [privacy, gdpr, retention, age-assurance, compliance-block]
      config: {path: skills/engineering/privacy-and-regulatory.md}
    - id: skill:mobile-native
      capabilities: [mobile, ios, android, store-review, accessibility, release]
      config: {path: skills/engineering/mobile-native.md}

Nothing consumes them. Confirmed on `dev`: no caller of `by_kind("skill")`
outside the model's own `enabled()` helper, and the catalogue's only consumer
is `GET /api/plan/registry`, which dumps every row verbatim. The skill *files*
are served independently by the skills service (`/api/pfactory/skills`,
`/.well-known/agent-skills/index.json`), so the rows add nothing today.

The cost is at the handover. PFactory raises an obligation — the compliance
lens cites GDPR retention duties, the mobile implicit requirements demand store
listing and OS floors — and then hands AIFactory a contract that says what must
be true but not that a written skill for exactly that work already exists in
this repo. The coding agent rediscovers it or doesn't.

## Proposed outcome

- At contract assembly, enabled `kind: skill` rows whose `capabilities` match
  what the contract actually needs are attached to `epic_context`, alongside
  the existing `house_standards`, `constitution` and `compliance` blocks.
- The block distinguishes three states, so a reader can tell them apart:
  the matcher never ran (feature absent), it ran and matched nothing, and it
  matched N skills. This is the `available: false` discipline the neighbouring
  attach helpers already follow.
- A contract with no matching skills is byte-identical to today apart from that
  block, so nothing downstream changes behaviour by accident.
- The rows become load-bearing: deleting a skill row, or its capabilities,
  changes what the handover carries — and a test says so.

## Affected users and systems

- `apps/backend/plan/emit/contract_emit.py` (assembly), a new attach module
  beside `constitution.py` / `house_standards.py`, `plan/registry/` (reading
  the lane), tests.
- AIFactory, which receives `epic_context` — additively; it ignores unknown
  keys.
- Not the skills service, not `/api/plan/registry`, not the skill files.

## Constraints

- Additive and best-effort: never raises, never blocks an emit, and an absent
  or unreadable catalogue leaves today's contract untouched.
- Matching must be conservative. A wrongly attached skill sends a coder to
  irrelevant guidance; a missed one is today's behaviour. Prefer missing.
- Keep the existing guard rails: `tests/test_registry_skill_entries.py` already
  proves every row's `config.path` resolves; the new attach needs the standard
  negative control (unwire the call site → a test goes red).
- No new dependency, no change to the registry schema.

## Open questions

1. **What the contract's "needs" are derived from.** Candidates present at
   assembly time: the compliance block's `data_classes` / `obligations`, the
   plan type (`mobile-app` → mobile capabilities), the tfactory lanes, the
   plan's target kind. Recommendation: start with the two the issue names —
   compliance block and plan type — and treat the derivation as one function
   with a table, so adding a signal later is a row, not a rewrite.
2. **What is attached per skill:** `id` + `title` + `config.path`, or also the
   served URL (`/api/pfactory/skills/...`)? Recommendation: id, title and path
   — the path is what a coding agent with the repo checked out can open, and
   the URL needs a base that the contract does not carry.
