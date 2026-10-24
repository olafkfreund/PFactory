---
status: approved
issue: 922
intent: intent/2026-10-24-922-drain-running-tasks.md
---

# Spec: the drain sees every running task, and the Helm chart drains too

## Design

**Endpoint** (`apps/web-server/server/main.py:547-553`). `held` becomes the
number of live lease watchers plus the number of unfinished agent processes:

```python
svc = agent_service._agent_service  # the singleton; None until first use
procs = svc.running_tasks.values() if svc else ()
held = sum(not t.done() for t in run_leases._WATCHERS.values()) + sum(
    p.returncode is None for p in procs
)
```

- Reading the module global instead of calling `get_agent_service()` keeps the
  probe from building an `AgentService` on a pod that never ran a task.
- `running_tasks` holds `asyncio.subprocess.Process`. `returncode is None`
  means the process is still running, so a process that has exited but not
  been popped yet is not counted.
- A task that holds both a lease and a process counts twice. Only `held > 0`
  matters to the preStop loop, and `held` stays a count of what the pod holds.
  The 200/503 contract and the response shape do not change.

**Chart** (`charts/pfactory`):

- `values.yaml`: `terminationGracePeriodSeconds: 1800` (was 45), and a new
  `drain:` block with `enabled: true` and `maxWaitSeconds: 1740`. The comment
  says the grace period must exceed `maxWaitSeconds`, which leaves 60s for the
  #804 relay teardown (~25s).
- `templates/deployment.yaml`: when `.Values.drain.enabled`, add
  `lifecycle.preStop.exec` to the main container, after the readiness probe.
  The command is factory-gitops#306's, with the wait and the port templated:
  `end=$(( $(date +%s) + <maxWaitSeconds> )); while [ $(date +%s) -lt $end ]; do curl -fs --max-time 5 http://localhost:<service.targetPort>/api/drain >/dev/null && exit 0; sleep 10; done; exit 0`.
- `values.schema.json` sets `additionalProperties: true`. If it lists
  top-level properties with types, add `drain` there too.

## Alternatives rejected

- **Deduplicate leases against `running_tasks` by task id.** Watcher keys are
  `(spec, run)` tuples, not task ids, so a join needs a mapping that does not
  exist. The double count changes nothing for the hook.
- **Call `get_agent_service()`.** It creates the singleton, and reads
  settings, as a side effect of a probe.
- **Keep the chart default at 45s and make the drain opt-in.** #922 asks for
  the same defaults as the gitops manifest. An idle pod still stops at once,
  because the loop exits on the first 200.

## Risks

- A wedged agent process that never exits keeps `held > 0`, so the pod waits
  the full 1740s on scale-down. The gitops manifest already accepts that bound.
- Chart users who relied on a 45s shutdown now wait up to 30 minutes while a
  task is running. This is intended. `drain.enabled: false` together with
  `terminationGracePeriodSeconds` restores the old behaviour.
- `curl` must be in the image. The gitops preStop already relies on it in the
  same image.

## Verification

- `tests/test_drain_endpoint.py`: new tests that a running process
  (`returncode None`) with no watcher gives 503 `{"held": 1}`, that a finished
  one (`returncode 0`) is not counted, and that an unset singleton gives 200.
- `tests/helm/test_p4_helm.py`: a new test that the default render has
  `terminationGracePeriodSeconds: 1800` and a preStop containing
  `--max-time 5`, `/api/drain` and `1740`, and that
  `--set drain.enabled=false` drops the hook.
- `helm lint --strict` and `helm template` pass.
- The pre-commit hook passes on every commit.
