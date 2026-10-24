---
status: approved
issue: 922
spec: spec/2026-10-24-922-drain-running-tasks.md
---

# Plan: the drain sees every running task, and the Helm chart drains too

Approved decisions, carried over from the spec:

- `/api/drain` counts live lease watchers plus unfinished agent processes
  (`returncode is None`) in `agent_service._agent_service.running_tasks`. It
  reads the module global, never `get_agent_service()`, so a probe never
  builds the service. A missing singleton counts 0.
- The response stays `{"held": n}` with 200 when `n == 0`, else 503. A task
  that holds both a lease and a process may count twice. That is accepted.
- Chart defaults match factory-gitops#306: `terminationGracePeriodSeconds: 1800`,
  `drain.enabled: true` and `drain.maxWaitSeconds: 1740`. The preStop command
  is #306's text, including `--max-time 5`, with the wait and the port
  (`.Values.service.targetPort`) templated.

## Steps

1. `apps/web-server/server/main.py:547-553` (`drain_check`): import
   `agent_service` next to `run_leases`, and add the unfinished-process count
   to `held`. Update the comment to say runs and agent processes.
   → `pytest tests/test_drain_endpoint.py`
   Traps: keep the function-local import (`# noqa: PLC0415`). The endpoint must
   stay in `TokenAuthMiddleware.PUBLIC_PATHS`, so do not touch `auth.py`.
2. `tests/test_drain_endpoint.py`: monkeypatch `agent_service._agent_service`
   to a `SimpleNamespace(running_tasks={...})` whose values carry
   `returncode`. Add these tests: running with no watcher gives 503
   `{"held": 1}`; finished (`returncode 0`) is not counted; an unset singleton
   (`None`) is 200. The existing tests also set the singleton to `None`, so a
   real service from another test cannot leak in.
   → `pytest tests/test_drain_endpoint.py -q` gives all passed.
   Traps: the import paths the file already sets up (`apps/backend` and
   `apps/web-server` on `sys.path`).
3. `charts/pfactory/values.yaml:46-51`: set `terminationGracePeriodSeconds`
   to 1800 and rewrite its comment (the drain waits up to `maxWaitSeconds`,
   the remaining 60s covers the #804 teardown). Add a `drain:` block below it
   with `enabled: true` and `maxWaitSeconds: 1740`.
   `charts/pfactory/values.schema.json`: add `drain` (object with `enabled`
   boolean and `maxWaitSeconds` integer >= 0).
   `charts/pfactory/templates/deployment.yaml`: after the readinessProbe block
   (~line 281), add
   `{{- if (.Values.drain).enabled }} lifecycle: preStop: exec: command: ["sh","-c", "<#306 command>"] {{- end }}`
   at the container indent.
   → `helm lint --strict charts/pfactory`, `helm template pfactory charts/pfactory | grep -A4 preStop`
   Traps: the template's `| default 45` stays, as a fallback when the key is
   unset. Quote the command with `printf ... | quote`, or write it as a
   literal so `$(...)` and `&&` survive YAML.
4. `tests/helm/test_p4_helm.py`: add
   `test_prestop_drain_on_by_default_and_toggleable`, in the style of the
   PDB test's `render()`. It asserts that the default render has
   `terminationGracePeriodSeconds: 1800`, `--max-time 5`, `/api/drain` and
   `1740`, and that `--set drain.enabled=false` has no `preStop`.
   → `pytest tests/helm -q -k prestop`
   Traps: mark `@pytest.mark.helm` and use the `helm_available` and
   `chart_dir` fixtures.
5. `CHANGELOG.md`: one bullet under the first `## Unreleased`.

## Tests

- `pytest tests/test_drain_endpoint.py tests/helm/test_p4_helm.py -q`: all pass.
- `helm lint --strict charts/pfactory`: 0 errors, 0 warnings.
- The pre-commit hook (full suite plus docker build) passes on the commit.

## Rollback

Revert the PR. Chart users can also set `drain.enabled: false` and
`terminationGracePeriodSeconds: 45` without a revert.
