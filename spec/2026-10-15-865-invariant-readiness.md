---
status: approved
issue: 865
intent: intent/2026-10-15-865-invariant-readiness.md
---

# Spec: invariants run in production — readiness, timer, metric

## Design

### Registry (`apps/backend/factory_invariants/__init__.py`)

- `_Registration` (lines 83-87) gains `critical: bool = False`.
- `register(module, check=None, *, reason=None, critical=False)` (lines 103-133).
  `critical=True` with `reason=` is a `ValueError`: a written reason has
  nothing to run.
- `check_all(self, *, critical: bool | None = None)` (lines 151-166): `None`
  runs every selected check (today's behaviour), `True` only critical ones,
  `False` only non-critical ones.
- `_from_env()` (lines 174-180): enabled unless `FACTORY_INVARIANTS` is one of
  `0`, `false`, `no` (case-insensitive); unset means on. `ONLY`/`SKIP` unchanged.
  The `InvariantRegistry` dataclass default stays `enabled=False`, so a registry
  built by hand in a test is still explicit.
- Module docstring: replace "DEFAULT OFF IN PRODUCTION" with the RFC-0024 §4
  rule (on by default, critical checks drive readiness, non-critical ones run
  on a timer).

### Runtime (`apps/web-server/server/background/invariants.py`, new)

Mirrors `server/background/liveness_sweep.py` (same backend `sys.path` insert,
a sync body plus an async loop):

- `load_companions() -> list[str]`: under `apps/backend` and `apps/web-server`
  (resolved from this file), import `<pkg>._invariants` for every
  `*/_invariants.py`, plus the root-level `*_invariants.py` sibling modules
  (today `factory_common_invariants`). An import that raises is logged at error
  level and skipped, so one broken companion cannot stop the service.
  Idempotent: a module already imported is not re-run.
- `VIOLATIONS = Counter("factory_invariant_violations_total", ..., ["service", "package"])`
  from `prometheus_client` (installed with the instrumentator). `package` is
  the top-level package (`module.split(".")[0]`), so cardinality is bounded by
  the package count (32).
- `run_once() -> list[Violation]`: `registry.check_all(critical=False)`; per
  violation, `logger.error(...)` naming the package and the detail, and
  `VIOLATIONS.labels(service="pfactory", package=...).inc()`.
- `invariant_loop(period_seconds)`: sleep, then `await asyncio.to_thread(run_once)`,
  forever; per-iteration exceptions logged and swallowed; cancellation propagates.
- `readiness() -> list[str]`: sorted failing packages from
  `registry.check_all(critical=True)`, cached for `READY_CACHE_SECONDS = 10`
  on `time.monotonic()`.

### Wiring

- `apps/web-server/server/config.py` (beside lines 93-96):
  `INVARIANTS_PERIOD_SECONDS: int = 300` (env `APP_INVARIANTS_PERIOD_SECONDS`).
- `apps/web-server/server/main.py` lifespan (lines 77-140): `load_companions()`
  at startup; if `registry.enabled`, start `invariant_loop` as
  `app.state.invariant_task`; cancel it on shutdown like the liveness task.
- `main.py` next to `/api/health` (line 449): `GET /api/ready` returns
  200 `{"status": "ready"}` or 503 `{"status": "not ready", "failing": [...]}`.
  `/api/health` is not touched.
- `apps/web-server/server/auth.py:227`: add `"/api/ready"` to `PUBLIC_PATHS`
  (probes carry no token).
- `apps/web-server/server/observability/metrics.py:28`: exclude `/api/ready`
  from HTTP metrics, as `/api/health` is (a probe every 10 s is noise).

No check is marked critical in this change (RFC-0024 §4.4: one release as
non-critical first), so `/api/ready` returns 200 until a package opts in.

## Alternatives rejected

- Failing `/api/health`: it is also the liveness probe (RFC §7).
- Stopping startup on a violation: one false positive takes the service down (RFC §7).
- Reusing the CI checker's discovery: the checker is a script outside the
  service's import path; a two-glob loader is smaller than sharing it.
- Hard-coding `factory_common_invariants` in the loader: the root-level
  `*_invariants.py` glob finds it without a second list to keep in sync.

## Risks

- Default-on runs every non-critical check in every pod every 5 minutes. They
  are local today; a broken check is reported as a violation, not raised.
- Tests now run with the process registry enabled by default; the pilot tests
  already set `enabled` explicitly.
- `/api/ready` is unused until factory-gitops#283 moves the readiness probe.

## Verification

- New `tests/test_invariant_runtime.py`:
  - `check_all(critical=True/False)` filters; `critical=True` with a reason raises.
  - `_from_env()` is on when unset and off for `0`.
  - `run_once()` logs at error and increments the counter for a failing
    non-critical check (a fresh registry monkeypatched into the module).
  - `readiness()` names the failing package and caches for 10 s.
  - `GET /api/ready` on `create_app()` without a token: 503 with the failing
    package, 200 when clean; `GET /api/health` keys unchanged. Fails on
    `origin/dev` (no route).
- `pytest tests/test_invariant_runtime.py tests/test_invariant_pilot.py tests/test_invariant_vendored_seams.py tests/test_web_liveness_sweep.py -q`.
- `ruff check`, `ruff format --check` on touched files.
