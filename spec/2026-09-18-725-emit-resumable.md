---
status: approved
issue: 725
intent: intent/2026-09-18-725-emit-resumable.md
---

# Spec: Live emit blocks the server and loses track of what it created

## Design

### 1. Save each issue number as it is created

`apps/backend/plan/emit/github_emitter.py` — `emit_to_github` gains one
keyword-only parameter:

    on_progress: Callable[[int, dict[str, int]], None] | None = None

It is called with `(epic_number, child_numbers_so_far)` once the epic exists
(created or reused) and again after each child issue is created. Reused
children are included in `child_numbers_so_far` from the start. A callback
failure is caught and logged as a warning — progress saving must never abort
issue creation. Dry run never calls it.

`apps/backend/plan/service.py` — `PlanService.emit` passes a callback that:

- sets `session.emitted_issue_number = epic_number`;
- sets `session.emit_result = EmitResult(dry_run=False, epic_number=...,
  child_numbers=dict(...)).model_dump()`;
- calls `self._save(session)` (already never raises; writes the durable row and
  the disk mirror).

The final write after `emit_to_github` returns is unchanged, so a completed emit
ends with exactly the state it has today.

After a kill, the session reads: `status: approved`, `emit_result.dry_run:
false`, the epic number, and the N children made so far. That combination is
the "partial live emit" signal the issue asked for: a never-emitted session
has `dry_run: true` or no `emit_result`; a finished one has `status: emitted`.
No new field is added.

Re-running emit then reuses the saved numbers through the existing #119 path
(`existing_epic_number` / `existing_child_numbers`) and creates only the rest.

### 2. Run the emit off the event loop

`apps/web-server/server/routes/plan_pipeline.py`:

- `emit`: `await asyncio.to_thread(SERVICE.emit, session_id, repo=..., ...)`.
  `_load_docs_connections` stays awaited on the loop before it (async DB).
- `emit_contract` (**decided, intent Q2**): same change.

Request and response are unchanged: the caller still waits for the result.
No admission cap: emit is I/O-bound `gh` calls, not the LLM/recon pipeline the
cap protects.

### 3. One emit per session at a time

Moving emit to a thread removes an accidental guarantee: today the blocked loop
means two emits of the same session cannot overlap in one pod. With threads, a
double-click (or portal + MCP) would run two emits that both see "no epic yet"
and both create one.

`PlanService` gets `self._emit_locks: dict[str, threading.Lock]` (created
under `_store_lock`). `emit` does a non-blocking acquire for the session; if it
is held, raise `PlanServiceError("an emit is already running for session
'<id>'")` → the route's existing 400. Released in `finally`. Covers
`emit_contract` too (same lock) so a contract emit cannot race an issue emit.
Dry runs take the lock as well: cheap, and keeps one code path.

In-process is enough: `replicaCount: 1` is pinned
(`charts/pfactory/values.yaml:31`, WebSocket fan-out limitation).

### 4. Label typo

`apps/backend/plan/synthesize/testing_strategy.py:266`: `handover:tfactory` →
`handoff:tfactory` (the label TFactory reads; the emitter's `_dedup` drops the
duplicate with the taxonomy label from `labels.py:132`).
`tests/test_synthesize.py:87` updated to match.

### Decided (intent Q1)

No "skip titles that already exist in the repo" check. The remaining window is
the moment between GitHub creating an issue and `_save` returning — at most one
duplicate after a kill, down from all of them.

## Alternatives rejected

- **Background task + polling endpoint**: changes the `/emit` contract for the
  portal, MCP tools, `parr-run` and CFactory; the intent requires the same
  synchronous contract. `to_thread` fixes the probe without it.
- **Raise the liveness `timeoutSeconds`/`failureThreshold`**: hides this and the
  next blocking call; rejected in the intent.
- **Save progress inside the gh runner**: the runner knows nothing about
  sessions; the emitter already owns the per-child loop.
- **New `EmitResult.in_progress` field**: a killed emit would leave it `true`
  forever, indistinguishable from one still running; the status + `dry_run` +
  counts combination already says what happened.
- **Title-dedup against the repo**: see Q1.

## Risks

- One `_save` per created issue: 35 durable-row upserts + disk writes for a
  34-child plan instead of 1. Small next to 35+ `gh` subprocess calls.
- `_save` from a worker thread: already the case for `process_async`, and `_save`
  serialises disk writes under `_store_lock`.
- A second emit of the same session now gets a 400 instead of running; that is
  the intended behaviour (it would have created duplicates).
- Session objects are mutated from the worker thread while `GET /{id}` may read
  them — same as `process_async` today; readers see a consistent-enough dict.

## Verification

- **Kill mid-emit, then resume** (the issue's case): a fake gh that creates the
  epic + 3 children, then raises a `BaseException` subclass (simulating SIGKILL;
  the emitter's `except Exception` does not catch it). Build a *new*
  `PlanService` from the same `store_dir` (simulating the restarted pod): the
  session has the epic and 3 child numbers, `dry_run: false`. Emit again with a
  fresh fake gh: it creates exactly the remaining children and no epic;
  total issues created across both runs == 1 + number of children.
- **Negative control**: remove the `on_progress` call site in `PlanService.emit`;
  the restarted service shows no numbers and the re-emit creates duplicates —
  test goes red.
- **Event loop stays free**: route test where `SERVICE.emit` blocks for 0.5s in
  a stub; a concurrent coroutine ticking every 10ms records ticks during the
  emit (> 20). Negative control: call `SERVICE.emit` directly (old code) → ~0
  ticks. Same for `emit_contract`.
- **Concurrent emits**: two threads emit the same session with a slow fake gh;
  exactly one runs, the other raises the "already running" error; epic created
  once.
- **Label**: `test_synthesize.py` asserts `handoff:tfactory` and that no child
  carries `handover:tfactory`.
- Existing emit suites green: `pytest tests -q -k "emit or synthesize"`, then
  the full suite via the pre-commit hook.
