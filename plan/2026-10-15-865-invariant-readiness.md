---
status: approved
issue: 865
spec: spec/2026-10-15-865-invariant-readiness.md
---

# Plan: invariants run in production — readiness, timer, metric

Implements RFC-0024 §4 (Factory#818) in PFactory. Approved decisions:

- `register(..., critical=False)`; a critical registration must be a check,
  not a reason. No existing check is marked critical (one release
  non-critical first, RFC §4.4).
- `check_all(*, critical=None|True|False)` filters by the flag; `None` keeps
  today's behaviour.
- `FACTORY_INVARIANTS` defaults on; `0`/`false`/`no` turn it off. `ONLY`/`SKIP`
  unchanged. The `InvariantRegistry` dataclass default stays `enabled=False`.
- `GET /api/ready`: critical checks, cached 10 s, 503 naming failing top-level
  packages, else 200. Public (no auth), excluded from HTTP metrics.
  **`/api/health` is not changed** (it is also the liveness probe).
- Non-critical checks run on an in-app timer, `APP_INVARIANTS_PERIOD_SECONDS`
  default 300. Each violation: `logger.error` naming the package, and
  `factory_invariant_violations_total{service="pfactory",package=<top-level>}` +1.
- Companions are imported at startup by globbing `*/_invariants.py` and
  root-level `*_invariants.py` under `apps/backend` and `apps/web-server`; a
  failing import is logged and skipped.
- Branch is based on `dev`, not on #864: nothing here needs #864's code. With
  #864 merged, the loader picks up its 29 extra companions with no change.
- factory-gitops (probe move, factory-gitops#283) is out of scope.

Python: `/mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/python`
(worktrees use the main clone's venv).

## Steps

1. `apps/backend/factory_invariants/__init__.py`
   - lines 18-24 (docstring "DEFAULT OFF IN PRODUCTION" paragraph): replace with
     "ON BY DEFAULT (RFC-0024 §4)": `FACTORY_INVARIANTS=0` turns checks off;
     critical checks drive `/api/ready`; the rest run on the service's timer.
   - lines 83-87 `_Registration`: add `critical: bool = False` (last field).
   - lines 103-133 `register`: add keyword `critical: bool = False`; if
     `critical and reason is not None` raise `ValueError` ("a written reason
     has nothing to run, so it cannot be critical"); pass it to `_Registration`.
   - lines 151-166 `check_all(self, *, critical: bool | None = None)`: also
     skip a registration when `critical is not None and reg.critical != critical`.
     Docstring: one line on the filter.
   - lines 174-180 `_from_env`: `enabled=os.environ.get("FACTORY_INVARIANTS", "1").strip().lower() not in {"0", "false", "no"}`;
     docstring "On unless FACTORY_INVARIANTS=0".
   → verify: `python -c` import, `InvariantRegistry(enabled=True)` with one
   critical and one plain failing check: `check_all(critical=True)` returns
   only the first.
   Traps: `apps/backend/factory_invariants/_invariants.py` probes the registry's
   own guard; run `pytest tests/test_invariant_vendored_seams.py` after.

2. `apps/web-server/server/background/invariants.py` (new). Model on
   `server/background/liveness_sweep.py:1-80` (docstring, `_BACKEND_PATH`
   `sys.path` insert, `logger`, sync body + async loop).
   - `_APPS = Path(__file__).resolve().parents[3]` (`apps/`); roots
     `_APPS / "backend"`, `_APPS / "web-server"`.
   - `load_companions() -> list[str]`: for each root, names
     `f"{p.parent.name}._invariants"` for `sorted(root.glob("*/_invariants.py"))`
     and `p.stem` for `sorted(root.glob("*_invariants.py"))`; `importlib.import_module`
     each inside `try/except Exception` → `logger.exception("invariant companion %s failed to import", name)`;
     return the names imported.
   - `VIOLATIONS = Counter("factory_invariant_violations_total", "Runtime invariant violations, by owning package (RFC-0024).", ["service", "package"])`.
   - `def _package(v: Violation) -> str: return v.module.split(".")[0]`.
   - `run_once() -> list[Violation]`: `registry.check_all(critical=False)`; per
     violation `logger.error("invariant violated in %s: %s", _package(v), v.detail)`
     and `VIOLATIONS.labels(service="pfactory", package=_package(v)).inc()`.
   - `async def invariant_loop(period_seconds: float) -> None`: `while True:
     await asyncio.sleep(period_seconds)`, then `await asyncio.to_thread(run_once)`
     in `try/except Exception` → `logger.exception`; `CancelledError` propagates.
   - `READY_CACHE_SECONDS = 10.0`; module `_ready_cache: tuple[float, list[str]] | None`.
     `readiness() -> list[str]`: if cached and `time.monotonic() - t < READY_CACHE_SECONDS`
     return cached; else `sorted({_package(v) for v in registry.check_all(critical=True)})`,
     store, return.
   - Use `from factory_invariants import Violation, registry` at module top
     (after the `sys.path` insert, `# noqa: E402` as liveness_sweep does).
     Read `registry` through the module (tests monkeypatch
     `server.background.invariants.registry`).
   → verify: `python -c "import server.background.invariants as m; print(m.load_companions())"`
   lists `factory_common_invariants`, `factory_invariants._invariants`,
   `pfactory_secrets._invariants`.
   Traps: `prometheus_client` registers globally; a second import of the
   module in one process is fine (module cache), but never construct the
   Counter inside a function.

3. Wiring.
   - `apps/web-server/server/config.py:94-96`: after the liveness settings add
     `INVARIANTS_PERIOD_SECONDS: int = 300  # non-critical invariant timer (RFC-0024)`.
   - `apps/web-server/server/main.py` lifespan, after the liveness block
     (line ~126, before `yield`): import `load_companions`, `invariant_loop`
     from `.background.invariants` and `registry` from `factory_invariants`;
     `load_companions()`; `app.state.invariant_task = None`; if
     `registry.enabled`: create the task with `settings.INVARIANTS_PERIOD_SECONDS`
     and log it. Shutdown (lines ~130-137): cancel and await it exactly like
     `sweep_task`.
   - `main.py` after the `/api/health` handler (line 449-458), do not edit it:
     ```python
     @app.get("/api/ready")
     async def readiness_check():
         from .background.invariants import readiness

         failing = await asyncio.to_thread(readiness)  # review: keep checks off the loop
         if failing:
             return JSONResponse(status_code=503, content={"status": "not ready", "failing": failing})
         return {"status": "ready"}
     ```
     (`JSONResponse` from `fastapi.responses`; check whether main.py already imports it.)
   - `apps/web-server/server/auth.py:227`: add `"/api/ready",` after `"/api/health",`
     with a one-line comment (readiness probe, no token).
   - `apps/web-server/server/observability/metrics.py:28`: default
     `excluded_handlers=("/metrics", "/api/health", "/api/ready")`.
   → verify: step 4 tests.
   Traps: the liveness-sweep shutdown pattern catches `CancelledError`; copy it.
   Do not touch `charts/` or any probe definition.

4. `tests/test_invariant_runtime.py` (new; header and `sys.path` setup like
   `tests/test_invariant_vendored_seams.py:18-31`, both roots):
   - `test_check_all_filters_by_critical`: fresh `InvariantRegistry(enabled=True)`,
     one critical + one plain failing check; `critical=True` → only the first,
     `False` → only the second, `None` → both.
   - `test_a_reason_cannot_be_critical`: `ValueError`.
   - `test_registry_is_on_unless_switched_off(monkeypatch)`: delenv →
     `fi._from_env().enabled` True; setenv `"0"` → False.
   - `test_run_once_logs_and_counts(monkeypatch, caplog)`: monkeypatch
     `server.background.invariants.registry` with a fresh enabled registry holding
     a failing non-critical check named `pkgx.mod`; `run_once()`; assert an
     ERROR record mentions `pkgx`, and
     `VIOLATIONS.labels(service="pfactory", package="pkgx")._value.get()` rose by 1.
   - `test_readiness_names_failing_package_and_caches(monkeypatch)`: failing
     critical check counting its calls; two `readiness()` calls → `["pkgx"]`
     and one run; monkeypatch `time.monotonic` forward 11 s → runs again.
     Reset `_ready_cache` to `None` via monkeypatch first.
   - `test_ready_endpoint(monkeypatch)`: `TestClient(create_app())` **without**
     `with` (no lifespan), auth left on (do not set `APP_DISABLE_AUTH`); with a
     failing critical registry → 503 and `failing == ["pkgx"]`; with a clean
     one → 200 `{"status": "ready"}`; `GET /api/health` → 200 and keys
     `{"status", "version", "provider_auth"}`. Reset `_ready_cache` between.
   → verify: `pytest tests/test_invariant_runtime.py -q`; prove the endpoint
   test fails without the step-3 route (temporarily remove it, run, restore).
   Traps: `tests/obs` wipes the Prometheus registry; read the counter through
   the `VIOLATIONS` object, not `REGISTRY`. If `create_app()` needs env the
   obs fixture sets (`tests/obs/test_p6_main_wiring.py:26-48`), mirror it,
   except `APP_DISABLE_AUTH`.

   Implementation notes: the repo's pre-commit hook ruff-autofixes staged
   files, which also modernised `auth.py` (`timezone.utc` → `UTC`) and
   `metrics.py` (`collections.abc.Iterable`, import order); behaviour is unchanged.
   The pilot companions' docstrings (`pfactory_secrets/_invariants.py`,
   `factory_common_invariants.py`) are updated for default-on.

## Tests

- `pytest tests/test_invariant_runtime.py tests/test_invariant_pilot.py tests/test_invariant_vendored_seams.py tests/test_web_liveness_sweep.py -q` → green.
- `ruff check` and `ruff format --check` on every touched or new file.

## Rollback

Revert the PR, or set `FACTORY_INVARIANTS=0` to switch all checks off without
a deploy of code. `/api/ready` is unused until factory-gitops#283.
