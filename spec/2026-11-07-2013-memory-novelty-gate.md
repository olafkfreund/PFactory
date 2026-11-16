---
status: approved
issue: 2013
intent: intent/2026-11-07-2013-memory-novelty-gate.md
---

# Spec: RFC-0021 Phase 1 — novelty gate on the live memory writers

## Design

Phase 1 ships the part of the novelty gate that cannot drop a distinct fact:
an **exact-repeat gate** (identical after trimming leading and trailing
whitespace) on the two live file writers, plus removal of the boilerplate
marker at its source. Identical change in AIFactory, PFactory and TFactory.

Why not near-duplicate (lexical cosine) gating: without an embedder, word
overlap does not track meaning. "Do not enable caching for X" vs "Enable
caching for X" (cosine 0.93), "A calls B" vs "B calls A" (cosine 1) and
`timeout < 30` vs `timeout > 30` (same `\w+` tokens) are all "near
duplicates" and opposite facts. The intent forbids suppressing a distinct
fact, and the pod has no embedder. The baseline's 80 near pairs are mostly the
`Implemented subtask: <id>` marker, which step 4 removes at its source. True
semantic gating waits for an embedder (named as a follow-up in the PR).

### 1. `apps/backend/memory/novelty.py` (new)

```python
def drop_repeats(candidates: list, seen: set[str], label: str) -> list:
    """New list: candidates whose .strip() is not in seen; adds kept ones to seen.
    Non-strings and empty strings pass through. Each rejection logs INFO."""
```

Only a string identical after trimming its ends is a repeat, so case,
operators, word order and internal whitespace (`PREFIX="a b"` vs
`PREFIX="a  b"`) all keep distinct facts distinct. The baseline's exact
measure also folds case and internal whitespace; those few extra pairs are
left alone on purpose.

### 2. `memory/sessions.py save_session_insights`

Gate these fields only, each **against the same field** in the other
`session_*.json` files of the same insights dir (the file being overwritten
is excluded) and against earlier items in the same list:

`what_worked`, `recommendations_for_next_session`,
`discoveries.patterns_found`, `discoveries.gotchas_encountered`.

Not gated: `what_failed` (an event record: a second failure of the same
subtask must stay recorded), `subtasks_completed`, `files_understood`. The
filter builds new lists and a new `discoveries` dict; the caller's `insights`
is not mutated. An unreadable session file is skipped, as `load_all_insights`
does. The filtered `session_data` is also what is forwarded to Graphiti when
enabled; that is intended (less noise) and is the only Graphiti effect.

### 3. `record_gotcha` tool (`agents/tools_pkg/tools/memory.py`)

Before appending, read `gotchas.md` if present, take its **last 1000
characters** (exactly the window `get_session_context` shows agents; both
use one module constant `GOTCHA_CONTEXT_CHARS = 1000`), split that into entry
bodies on the `## [timestamp]` header lines, and compare the stripped
would-be body (the gotcha plus its `_Context: …_` line, exactly as it would be
written) with each stripped existing body. A gotcha that has scrolled out of
the window is appended again, so re-recording still brings it back into view;
an entry cut by the window edge does not match, so it is appended (safe side). Context is part of the identity: the same
gotcha for a different endpoint is kept. On a match: do not write, log INFO,
and return `Gotcha already recorded: <gotcha>` so the agent knows it landed.
Otherwise append exactly as today.

Each writer checks its own store only. A gotcha recorded by the tool and also
listed in a session's `gotchas_encountered` is stored in both, as today: they
are different formats with different readers.

### 4. `agents/memory_manager.py`

`what_worked` becomes `[]`; the `Implemented subtask: <id>` marker is no
longer stored (the id stays in `subtasks_completed`). This dict also feeds the
Graphiti path when enabled; the marker carries no lesson there either.
`what_failed`'s `Failed to complete subtask: <id>` is unchanged.

### Visibility

Rejections log at INFO on `memory.novelty`. In AIFactory `cli/main.py`
calls `logging.basicConfig(level=logging.INFO)` when root has no handlers, so
the line reaches the build log. PFactory and TFactory configure no logging, so
there it is dropped like every other backend INFO line; their logging setup is
a named follow-up, not Phase 1. With exact-repeat gating there is no threshold
to mistune, which was RFC §7's reason for visibility.

## Alternatives rejected

- **Lexical cosine at 0.88, with or without a token-subset guard.** Merges
  opposite facts (above).
- **Embedder cosine (RFC §3.2).** No embedder in the pod; dead code path.
- **Gate `memory/patterns.py`.** Dead code; changes nothing live.
- **Dedup at the project pool merge.** Rewrites stored files and needs a JSON
  merge rule; the write-time gate stops new repeats without touching stored data.
- **One comparison set across all fields.** The same text means different
  things under `what_worked` and `what_failed`.
- **Shared package for the three repos.** The memory tree is vendored; the same
  small file is copied three times instead.

## Risks

- Near-duplicate paraphrases still accumulate. Phase 1 removes the exact
  repeats and the dominant boilerplate; the rest needs an embedder.
- Existing stored duplicates stay; Phase 1 only stops new ones.
- Drift between repos: identical `novelty.py` and identical tests in all three.

## Verification

- Tests (`tests/test_memory_novelty.py`, same file in each repo):
  - `drop_repeats`: repeat with different end whitespace dropped; in-batch
    repeat dropped; `timeout < 30` vs `> 30`, "do not X" vs "X", different
    case and different internal whitespace kept; non-string passes through;
    input list not mutated.
  - `save_session_insights`: a `what_worked` string from session 1 is dropped
    from session 2; the same string under `what_failed` is kept;
    `subtasks_completed` and the caller's dict are unchanged.
  - `record_gotcha`: a repeat is not appended; same gotcha with a different
    context is appended; a new gotcha is appended; a repeat of a gotcha
    outside the last 1000 characters is appended.
  - `memory_manager` file path: stored insights contain no `Implemented subtask:`.
- Mutation: make `drop_repeats` return `list(candidates)`; the drop tests go
  red. Restore the marker in `memory_manager`; its test goes red.
- Each repo's existing test suite and `ruff` stay green.
- Live (after deploy, user-owned): re-run `scripts/memory_dup_baseline.py` on
  `aifactory-demo` after three new builds; exact pairs among items written
  after the deploy should be near zero (the script folds case and whitespace,
  the gate does not).
