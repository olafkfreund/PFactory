---
status: approved
issue: 2013
spec: spec/2026-11-07-2013-memory-novelty-gate.md
coders:
  1: codex   # small, tricky logic: per-field comparison, no caller mutation, file being overwritten excluded
  2: codex   # small, tricky logic: tail-window parsing of gotchas.md and context-aware identity
  3: coder   # one-line change plus its test fixture; routine
  4: coder   # new test file against an exact list of cases; routine
  5: agy     # large mechanical: copy two files verbatim and replay steps 1-3 into PFactory and TFactory
reviewers:
  - codex    # independent read of the diff for edge cases in the gate logic
  - agy      # independent read for cross-repo drift between the three copies
---

# Plan: RFC-0021 Phase 1 — novelty gate on the live memory writers

Epic olafkfreund/Factory#389, issue AIFactory#2013. Canonical artifacts live in
AIFactory; the PFactory and TFactory PRs carry copies and link here.

## Approved decisions (from the spec)

- **Exact-repeat gate only.** A candidate is a repeat iff `c.strip()` equals
  the `.strip()` of an earlier item. No case folding, no whitespace collapsing,
  no cosine, no embedder (lexical similarity merges opposite facts; no
  embedder in the pod).
- **Session insights** (`memory/sessions.py save_session_insights`): gate only
  `what_worked`, `recommendations_for_next_session`,
  `discoveries.patterns_found`, `discoveries.gotchas_encountered`, each against
  **the same field** in the other `session_*.json` files of the same insights
  dir (exclude the file about to be overwritten) and against earlier items in
  the same list. Do **not** gate `what_failed`, `subtasks_completed`,
  `files_understood`. Build new lists and a new `discoveries` dict: never
  mutate the caller's `insights`. Skip unreadable/invalid JSON files. Non-string
  and empty items pass through. The filtered `session_data` also goes to
  Graphiti; intended.
- **`record_gotcha`**: dedup only within the last `GOTCHA_CONTEXT_CHARS = 1000`
  characters of `gotchas.md` (the window `get_session_context` shows; both use
  the constant). Split that tail on `## [timestamp]` header lines and **discard
  the first segment** (text before the first complete header in the tail: the
  file preamble or an entry cut by the window edge must never match). Compare the
  stripped would-be body (gotcha, plus `\n\n_Context: {context}_` when context
  is non-empty, exactly as written today) with each stripped existing body. On
  a match: no write, return text `Gotcha already recorded: {gotcha}`, and log
  through `memory.novelty` (call `drop_repeats([body], seen, "gotchas.md")`
  with `seen` = the stripped tail bodies, so the log line has one home).
- **`memory_manager.py`**: `what_worked` becomes `[]`. `what_failed` unchanged.
- **Logging**: rejections `logger.info("memory novelty gate: rejected %r in %s", item, label)`
  on `logging.getLogger(__name__)` in `memory/novelty.py` (`memory.novelty`).
  No logging-config changes in any repo.
- Each writer checks its own store only. Existing stored duplicates are left alone.

## Steps

1. **AIFactory** `apps/backend/memory/novelty.py` (new) and
   `apps/backend/memory/sessions.py:25-80`.
   `novelty.py`: `drop_repeats(candidates: list, seen: set[str], label: str) -> list`
   per the decisions (adds kept stripped strings to `seen`, returns a new list).
   `sessions.py`: before `session_data` is built (line 63), load the other
   session files in `insights_dir` (skip `session_file` itself), and build the
   four gated fields with `drop_repeats`, one `seen` set per field seeded from
   that field's strings in the prior files. Keep the docstring.
   → verify: `cd apps/backend && .venv/bin/python -c "from memory.novelty import drop_repeats; print(drop_repeats(['a',' a','b'], set(), 't'))"` prints `['a', 'b']`.
   Traps: ruff line length differs per repo (AIFactory wraps at 88); `discoveries`
   may be missing keys or be a caller-supplied dict — copy, do not mutate.

2. **AIFactory** `apps/backend/agents/tools_pkg/tools/memory.py:24` (module
   constant `GOTCHA_CONTEXT_CHARS = 1000`), `:117-147` (`record_gotcha` gate
   before the `open(gotchas_file, "a")` at line 135), and `:202` (gotchas
   slice in `get_session_context` uses the constant; leave the patterns slice
   at `:217` as is). Use `drop_repeats` for the comparison (see decisions).
   → verify: step 4 tests.
   Traps: the tool body is wrapped in `try/except Exception` returning an error
   text; keep the read inside it. `gotchas.md` may not exist yet.

3. **AIFactory** `apps/backend/agents/memory_manager.py:302`: `"what_worked": [],`.
   → verify: `grep -n "Implemented subtask" apps/backend/agents/memory_manager.py` returns nothing.
   Traps: leave `analysis/insight_extractor.py`'s `approach_used` alone (not stored as `what_worked`).

4. **AIFactory** `tests/test_memory_novelty.py` (new). Cases, each its own test:
   - `drop_repeats`: end-whitespace repeat dropped; in-batch repeat dropped;
     `"timeout < 30"` vs `"timeout > 30"`, `"Do not enable caching"` vs
     `"Enable caching"`, `"Use Foo"` vs `"use foo"`, `'PREFIX="a b"'` vs
     `'PREFIX="a  b"'` all kept; non-string kept; input list not mutated.
   - `save_session_insights` (tmp_path spec dir), **parametrized over all four
     gated fields**: a string in session 1 is dropped from that field in
     session 2; an in-list repeat is dropped. Plus: the same text in two
     different gated fields is kept in both; same string under `what_failed`
     kept; `subtasks_completed` and `files_understood` kept; `discoveries`
     missing keys works; a corrupt `session_*.json` in the dir is skipped;
     the caller's dict, including nested `discoveries` lists, is unchanged
     (deep-copy before, compare after); re-saving the same session number does
     not drop its own items; `caplog` shows an INFO record on `memory.novelty`.
   - `record_gotcha`: `tests/conftest.py:80-83` replaces `claude_agent_sdk`
     with a MagicMock, so the `@tool` decorator is a mock. In the test,
     `monkeypatch.setattr(memory_tools, "tool", lambda *a, **k: (lambda f: f))`
     and `monkeypatch.setattr(memory_tools, "SDK_TOOLS_AVAILABLE", True)` on the
     imported `agents.tools_pkg.tools.memory` module before calling
     `create_memory_tools(tmp_path, tmp_path)`; pick the function named
     `record_gotcha` and `asyncio.run` it. Assert on `gotchas.md` contents and
     the returned text: repeat not appended (and returns `Gotcha already
     recorded`); same gotcha with different context appended; new gotcha
     appended; a repeat whose original lies before the last 1000 characters
     is appended; a candidate equal to the **suffix** of an entry cut by the
     window edge is appended; `caplog` INFO on `memory.novelty` for the repeat.
   - `memory_manager`: `asyncio.run(save_session_memory(spec_dir, project_dir,
     "subtask-1", 1, True, ["subtask-1"]))` with
     `monkeypatch.setattr(memory_manager, "is_graphiti_enabled", lambda: False)`
     (imported at `memory_manager.py:22`); assert it returns `(True, "file")`
     and the saved session file has `what_worked == []` and
     `subtasks_completed == ["subtask-1"]`; with `success=False`, `what_failed`
     still holds `Failed to complete subtask: subtask-1`. Do not call the file
     fallback directly: the marker is built before it.
   → verify: `apps/backend/.venv/bin/python -m pytest tests/test_memory_novelty.py -q` green.
   Traps: `tests/conftest.py` puts `apps/backend` on `sys.path`; use `asyncio.run`
   or the repo's existing async test style for the handler.

5. **PFactory and TFactory** (worktrees `PFactory-2013`, `TFactory-2013`, branch
   `feat/2013-memory-novelty-gate` from `origin/dev`, the integration branch all three repos merge features into; deviation: the draft said `origin/main`): copy `novelty.py` and
   `tests/test_memory_novelty.py` verbatim from AIFactory; replay steps 1-3 at
   PFactory `sessions.py:25-76`, `memory.py:110-128,188`, `memory_manager.py:288`
   and TFactory `sessions.py:25-80`, `memory.py:114-132,197`,
   `memory_manager.py:294`. Copy the three artifacts into each repo's
   `intent/ spec/ plan/` unchanged.
   → verify: step 6 in each repo.
   Traps: PFactory wraps lines longer (its `sessions.py` is one-line signatures);
   match each repo's own ruff config. Do not "sync" unrelated drift between repos.

6. **Me (no file edits)**: in each repo run the new test file, the full
   backend suite as CI runs it, and `ruff check` on the CI paths. Mutation:
   make `drop_repeats` `return list(candidates)` → drop tests red; restore the
   marker in `memory_manager` → its test red; revert both.

## Deviations during implementation

- **Extractor-shaped discoveries are gated too (final Opus review).** On the
  main coder path `agents/session.py` passes `analysis/insight_extractor.py`'s
  output as `discoveries`, whose keys are `patterns_discovered`,
  `gotchas_discovered` (lists of dicts) and `recommendations` (strings), not
  `patterns_found`/`gotchas_encountered`. Gating only the planned keys left the
  busiest path ungated, against the intent's outcome. Those three keys are now
  gated the same way (same key across sessions, in-list repeats). A dict entry
  is compared by canonical JSON (`json.dumps(sort_keys=True)`), so key order
  does not matter and any differing value keeps it. `memory/novelty.py` gains
  `repeat_key()` for this.
- **Values are stored as given when they are not lists/dicts.** A gated field
  holding a string or `None`, or a non-dict `discoveries`, is passed through
  unchanged (it used to be iterated or raise). `discoveries` keeps exactly the
  caller's keys; missing keys are no longer filled in.
- **PFactory/TFactory test files** are the AIFactory tests run through each
  repo's own ruff (`--fix` and `format`), not byte-identical copies.

## Tests

- `apps/backend/.venv/bin/python -m pytest tests/test_memory_novelty.py -q` → all pass, each repo.
- `apps/backend/.venv/bin/python -m pytest tests -q -x` (or the repo's CI
  command) → no new failures versus the base branch.
- `apps/backend/.venv/bin/ruff check apps/backend tests` → clean.
- Mutations above → red, then green after revert.

## Left for the user (PR body, not code)

- Semantic (near-duplicate) gating needs an embedder in the pod; follow-up.
- PFactory and TFactory configure no logging, so `memory.novelty` INFO is
  dropped there; follow-up.
- After deploy: re-run `scripts/memory_dup_baseline.py` on `aifactory-demo`
  after three new builds and compare with the baseline.

## Rollback

Revert the three PRs. No data migration: stored files keep their format, and
nothing already stored was changed.
