---
status: approved
issue: factory-gitops#303
spec: spec/2026-10-24-303-pfactory-drain-endpoint.md
---

# Plan: PFactory pods drain running runs before they stop

## Approved decisions

- New public endpoint `GET /api/drain` in the PFactory web server. It counts the
  live watcher tasks in `run_leases._WATCHERS` and returns 200 `{"held": 0}`
  when none are live, otherwise 503 `{"held": n}`. No DB query, no auth.
- `"/api/drain"` joins `TokenAuthMiddleware.PUBLIC_PATHS`.
- `/api/ready` and the lifespan shutdown do not change. KEDA settings do not change.
- factory-gitops: pod `terminationGracePeriodSeconds: 1800` and a preStop exec
  loop on the `pfactory` container that polls `/api/drain` every 10s and gives
  up after 1740s (60s before the grace ends, so SIGTERM still arrives and the
  run is marked `failed`, not left `active`).
- PFactory: one PR into `dev`. Gitops: one DRAFT PR into `main`, titled
  "... (merge only after the PFactory release with /api/drain is live)",
  body `Refs #303`. Nobody merges the gitops PR in this lane.
- Plan jobs (`job_states`) are out of scope.

## Worktrees

- PFactory (base `origin/dev`): `/tmp/claude-1000/-mnt-code-Source-home-GitHub-Factory/8787aafd-2d97-443b-ae9a-8faa12f38be2/scratchpad/lanes/pf-drain`
- factory-gitops (base `origin/main`): `/tmp/claude-1000/-mnt-code-Source-home-GitHub-Factory/8787aafd-2d97-443b-ae9a-8faa12f38be2/scratchpad/lanes/gitops-drain`
- Branch in both: `fix/303-pfactory-drain-endpoint`.

## Steps

1. `apps/web-server/server/main.py:535-545` (after the `/api/ready` handler,
   before the "Mount static files" comment): add

   ```python
   # Drain: preStop polls this until the pod holds no run lease (factory-gitops#303).
   @app.get("/api/drain")
   async def drain_check() -> JSONResponse:
       from .services import run_leases  # noqa: PLC0415

       held = sum(not t.done() for t in run_leases._WATCHERS.values())
       return JSONResponse(status_code=503 if held else 200, content={"held": held})
   ```

   → verify by step 3's test.
   Traps: import lazily inside the handler, as `/api/ready` does, so app import
   order does not change. Read `_WATCHERS` through the module (`run_leases._WATCHERS`),
   not `from ... import _WATCHERS`, so a test's monkeypatch takes effect. Count
   `not t.done()`, not `len()`: a finished watcher may still be in the dict
   until its done-callback runs.

2. `apps/web-server/server/auth.py:228` (after `"/api/ready",  # readiness probe, no token`):
   add `"/api/drain",  # preStop drain poll, no token; exposes only a count`.
   → verify by step 3's test.
   Traps: exact path, not a prefix.

3. New `tests/test_drain_endpoint.py`, copying the `sys.path` preamble of
   `tests/test_invariant_runtime.py:18-27` and its `TestClient(create_app())`
   without `with` (no lifespan). Tests:
   - empty `_WATCHERS` → 200 `{"held": 0}`;
   - one not-done task → 503 `{"held": 1}`; plus one done task → still `{"held": 1}`;
   - no `Authorization` header in any request (proves it is public);
   - `"/api/drain" in TokenAuthMiddleware.PUBLIC_PATHS`, as in
     `tests/test_well_known_agent_skills.py:287`.
   Use `monkeypatch.setattr(run_leases, "_WATCHERS", {...})`. For the tasks,
   use plain stand-ins with a `done()` method (e.g. a tiny class or
   `types.SimpleNamespace(done=lambda: False)`) rather than real asyncio tasks,
   so no event loop is needed.
   → verify by `apps/backend/.venv/bin/pytest tests/test_drain_endpoint.py tests/test_invariant_runtime.py tests/test_well_known_agent_skills.py -q` (all pass).
   Traps: the file must be collected by CI's `pytest tests/` (it is, under
   `tests/`); do not add it to `uncollected-tests-allowlist.toml`.

4. `CHANGELOG.md:3` under `## Unreleased`: one bullet, e.g.
   "**`GET /api/drain` reports whether the pod still holds a run (factory-gitops#303).**
   200 `{"held":0}` when idle, 503 `{"held":n}` otherwise; public, for a preStop hook."
   → verify by reading the diff.
   Traps: none.

5. Commit, push, open the PFactory PR into `dev`, linking intent/spec/plan and
   `Refs olafkfreund/factory-gitops#303`. → verify CI `backend (ruff + pytest)` is green.
   Traps: never `--no-verify` on this code commit; hooks may take 10+ minutes.

6. factory-gitops worktree, `apps/pfactory/manifests/manifests.yaml`:
   - pod spec, directly above `securityContext:` at :58 (same 6-space indent):
     `terminationGracePeriodSeconds: 1800` with a one-line comment saying the
     preStop drain needs it and why 1800.
   - `pfactory` container, after `livenessProbe:` at :431 (same 10-space indent):

     ```yaml
     lifecycle: { preStop: { exec: { command: ["sh","-c","end=$(( $(date +%s) + 1740 )); while [ $(date +%s) -lt $end ]; do curl -fs http://localhost:3114/api/drain >/dev/null && exit 0; sleep 10; done; exit 0"] } } }
     ```
   → verify by `kubectl apply --dry-run=client -f apps/pfactory/manifests/manifests.yaml`
   (or `kubectl kustomize apps/pfactory` if a kustomization exists) and by
   checking the hook sits under the `pfactory` container, not an init container.
   Traps: the `securityContext` at :58 is the POD one; the one at :249 is the
   container's. `1740 < 1800` must hold. No cluster apply from this lane.

7. Commit, push, open the gitops PR as a DRAFT into `main`, title
   "feat(pfactory): drain running runs before a pod stops (merge only after the PFactory release with /api/drain is live)",
   body `Refs #303`, link the PFactory PR. Do NOT merge, do NOT mark ready.
   → verify `gh pr view --json isDraft` is `true`.
   Traps: merging before the image serves `/api/drain` makes every shutdown wait ~29 minutes.

## Tests

- `apps/backend/.venv/bin/pytest tests/test_drain_endpoint.py -q` → all pass.
- `apps/backend/.venv/bin/pytest tests/ apps/web-server/tests/ -m "not slow" -q` → no new failures.
- `ruff check apps/web-server/server/main.py apps/web-server/server/auth.py tests/test_drain_endpoint.py` → clean.
- Live (after release, not in this lane): long task on one pod, delete that pod,
  it stays Terminating until the task finishes, then exits; no "could not
  persist interrupted run". A task longer than the grace ends `failed`, not `active`.
  Also confirm `sh`, `date` and `curl` run in the `pfactory` container.

## Rollback

- PFactory: revert the PR; the endpoint is additive and unused until gitops lands.
- Gitops: revert the manifest commit; pods go back to the 30s default grace.
  Revert gitops first if both must go.
