---
status: approved
issue: 874
spec: spec/2026-10-15-874-ready-singleflight.md
---

# Plan: guard /api/ready against hung checks, and bound the timer period

Approved decisions (from the spec):

- `readiness()` becomes `async`, with at most one in-flight critical run
  (`_ready_run`), shared by concurrent probes and by probes after a timeout
  while the run is still hung.
- The run is awaited through `asyncio.shield` under `READY_TIMEOUT_SECONDS = 3.0`.
  On timeout, every package owning a selected critical check is failing; each
  is logged at error level and counted in `factory_invariant_violations_total`.
- The result (failing or timed out) is cached 10 s, as now.
- `/api/ready` awaits `readiness()` directly; `/api/health` is untouched.
- The registry gains `selected(*, critical=None) -> list[str]`, and
  `check_all` iterates it.
- `INVARIANTS_PERIOD_SECONDS = Field(300, ge=30)`: rejected, not clamped.
- core's reason gains one clause naming the two one-shot flags.
- Nothing is marked critical.

## Steps

1. `apps/backend/factory_invariants/__init__.py:156-176`: add `selected()`
   (sorted names with `check is not None`, `_selected(name)`, matching
   `critical` if given) and make `check_all` loop over it.
   → `apps/backend/.venv/bin/pytest tests/test_invariant_runtime.py tests/test_invariant_pilot.py -q`
   Traps: keep `check_all`'s "a raising check is a violation" behaviour.
2. `apps/web-server/server/background/invariants.py:38-93`: add
   `READY_TIMEOUT_SECONDS`, `_ready_run`, make `readiness()` async as above.
   Update the module docstring line about readiness.
   → same pytest command.
   Traps: `TimeoutError` is the builtin (py311+, ruff UP041). Use `now` from
   before the run for the cache timestamp, as the current code does.
3. `apps/web-server/server/main.py:484-488`: `failing = await readiness()`;
   drop the `to_thread` comment. `asyncio` is still used elsewhere in the file.
   → `pytest tests/test_invariant_runtime.py::test_ready_endpoint`
4. `apps/web-server/server/config.py:97`: `Field(300, ge=30)`.
   → new settings test.
   Traps: pydantic validates in `super().__init__` before token generation, so
   the failing case writes no files.
5. `apps/backend/core/_invariants.py:7-13`: add the one-shot-flags clause.
   → `pytest tests/test_invariant_pilot.py -q` (reason word count check).
6. `tests/test_invariant_runtime.py`: convert the cache test to
   `asyncio.run(inv.readiness())`; add a hung-check test (check blocks on a
   `threading.Event`, timeout patched to 0.2 s, two concurrent calls via
   `asyncio.gather` → one run started, both return `["pkgx"]`, counter +1 each
   call... counted once per timed-out evaluation; a further call after the
   cache expires still reuses the hung run); add the period bound test.
   Release the event at the end so the thread exits.
   → run the test against the old `invariants.py` to prove it fails.

## Tests

`apps/backend/.venv/bin/pytest tests/test_invariant_runtime.py tests/test_invariant_pilot.py tests/test_invariant_vendored_seams.py -q`
all pass; `ruff check` and `ruff format --check` on the touched files are clean.

## Rollback

Revert the implementation commits; the endpoint returns to the unguarded
thread-per-probe readiness.
