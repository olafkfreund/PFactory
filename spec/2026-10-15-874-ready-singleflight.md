---
status: draft
issue: 874
intent: intent/2026-10-15-874-ready-singleflight.md
---

# Spec: guard /api/ready against hung checks, and bound the timer period

## Design

### Single-flight readiness with a timeout (`apps/web-server/server/background/invariants.py`)

`readiness()` becomes `async`. It keeps the 10 s cache (`READY_CACHE_SECONDS`)
and adds a module-level `_ready_run: asyncio.Future | None`, the one thread
running `registry.check_all(critical=True)`:

1. Cache fresh → return it (unchanged).
2. If `_ready_run` is `None` or done, start a new one with
   `asyncio.ensure_future(asyncio.to_thread(...))`. Otherwise reuse it: a
   concurrent probe, or a probe after a timed-out run that is still hung,
   never starts a second thread.
3. Await it with `asyncio.wait_for(asyncio.shield(_ready_run),
   READY_TIMEOUT_SECONDS)`, where `READY_TIMEOUT_SECONDS = 3.0` (the kubelet
   probe timeout is 5 s). `shield` keeps one waiter's timeout from cancelling
   the shared run.
4. On timeout, the result is every package that owns a selected critical
   check: we cannot tell which check in the sequential run is stuck, and every
   one of them is unverified. Each is logged at error level ("did not finish
   within 3s") and counted in `factory_invariant_violations_total`, the same as
   a timer violation.
5. The result, failing or timed out, is cached for 10 s as before. All of this
   runs on the event loop thread, so no lock is needed.

`/api/ready` (`apps/web-server/server/main.py:488`) awaits `readiness()`
directly instead of wrapping it in `asyncio.to_thread`.

To list the critical packages without reaching into a private field, the
registry gains `InvariantRegistry.selected(*, critical=None) -> list[str]`
(`apps/backend/factory_invariants/__init__.py`): the sorted module names with a
real check that `check_all` would run. `check_all` iterates it, so the two
cannot drift.

### Timer lower bound (`apps/web-server/server/config.py:97`)

`INVARIANTS_PERIOD_SECONDS: int = Field(300, ge=30)`. A value below 30 fails
settings validation at startup, the same as any other bad setting, rather than
being silently clamped.

### core reason (`apps/backend/core/_invariants.py`)

Add one clause: besides the caches, `fast_mode._fast_mode_atexit_registered`
and `workspace/setup._git_hook_check_done` are one-shot flags whose only effect
is to skip repeating a registration or a check.

## Alternatives rejected

- **Run each critical check in its own thread to name only the hung one.** One
  hung check per probe interval would leak a thread each, which is the problem
  being fixed.
- **Clamp the period instead of `ge=30`.** A silent clamp hides a
  misconfiguration; a validation error names the setting. Matches the issue's
  first suggestion.
- **Cancel the hung thread.** Python cannot cancel a running thread.

## Risks

- A genuinely hung check keeps one executor thread forever. That is the bound
  this change sets (one, not one per probe).
- Changing `readiness` to `async` breaks any sync caller. The only caller is
  `main.py:488`; the tests are updated.

## Verification

- New tests in `tests/test_invariant_runtime.py`: two concurrent `readiness()`
  calls with a blocking critical check start one run and both get 503-worthy
  output naming the package after the timeout; a probe after the timeout does
  not start a second run; `Settings(INVARIANTS_PERIOD_SECONDS=0)` raises.
- The existing readiness/cache and `/api/ready` + `/api/health` tests still pass.
- `ruff check`, `ruff format --check` on touched files.
