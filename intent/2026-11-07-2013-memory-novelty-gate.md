---
status: approved
issue: 2013
author: olafkfreund
---

# Intent: RFC-0021 Phase 1 — novelty gate on the live memory writers

Epic: olafkfreund/Factory#389 (RFC-0021 §3.2 gate 3, §5 Phase 1).

## Problem

Since Phase 0, memory survives a task and pools per project. The baseline
(#1856, `docs/dev/memory-baseline-2026-10.md`) shows the pool is mostly
repetition: `aifactory-demo` has 33 items with 19 exact and 80 near-duplicate
pairs (lexical cosine >= 0.88, a measure, not an approved rejection
threshold; most near pairs are the boilerplate below); `pfactory-friends-demo`
14 items, 11 exact. Duplicates compete for the small slice of memory that
reaches a prompt (`get_session_context` shows the last 1000 characters of
`gotchas.md`), so they displace the few real lessons.

Two facts change where the fix goes:

- The RFC names `memory/patterns.py` as the target. That is dead code (no real
  call sites; Factory#389, first comment). The live writers are the
  `record_gotcha` agent tool (`agents/tools_pkg/tools/memory.py`), which appends
  with no dedup at all, and `memory/sessions.py save_session_insights`, which
  writes every insight string again each session.
- 15 of the 33 items are `Implemented subtask: <id>`, a generated completion
  marker from `agents/memory_manager.py` that carries no lesson.
- Both writers write into the spec's `memory/` dir, which
  `agents/utils.py seed_memory_from_project` pre-fills from the project pool.
  So "the store" for a comparison is that dir: it already holds the pooled
  project memory.

## Proposed outcome

- A memory candidate that repeats one already in the spec's `memory/` dir
  (pooled project memory included), or an earlier candidate in the same
  batch, is not written again by either file writer.
- The id-only `Implemented subtask: <id>` line is no longer stored.
- A rejected candidate is logged with what it matched, so a bad threshold is
  visible, not silent (RFC §7).
- Re-running `scripts/memory_dup_baseline.py` on `aifactory-demo` after at
  least three new builds shows a lower duplicate-pair-per-item ratio than the
  baseline (19+80 pairs / 33 items).

## Affected users and systems

`apps/backend/memory/` and `apps/backend/agents/` in AIFactory, PFactory and
TFactory (vendored trees). Every build, planning and verification session that
writes memory. No API, task-contract or storage-format change.

## Out of scope

The Graphiti write path in `memory_manager.py` (it returns before the file
writers). Graphiti is disabled in production (no `GRAPHITI_*` in the pod), and
has its own entity dedup; gating it waits until it is enabled.
`memory/patterns.py` (dead code).

## Constraints

- Must land in all three repos together, or the trees drift (RFC §7).
- Must not need an embedder: the live pod has none configured.
- Must not suppress a distinct fact that differs by one word ("read path" vs
  "write path", RFC §7).
- No new dependency. Existing memory files stay readable as they are.
- The gate is verified by mutation: disable it and a test goes red (RFC §6).

## Open questions

None. Approved unseen by user 2026-11-07, bulk run.
