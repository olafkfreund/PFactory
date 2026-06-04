# End-to-end smoke — 9 verification scenarios

> Run the full PFactory pipeline against a real AIFactory feature with a
> real Claude API key, real Docker, real `gh`, and a real git remote.
> The unit + integration tests under `tests/test_*.py` mock the SDK + the
> docker runner so they cost nothing to run; this smoke fires the actual
> wires and is what gives us confidence the MVP works end-to-end.

This guide is the operator counterpart to [`scripts/e2e-smoke.sh`](../scripts/e2e-smoke.sh).
Read this once before you run; refer back to the troubleshooting + phase-2 backlog
sections when something surprises you.

## When to run

- **Before tagging `v0.1.0-mvp`** — Task 12 (#13) blocks on this passing once
  end-to-end against at least one real AIFactory feature.
- **After landing changes to any of the four agents** (Planner / Gen-Functional /
  Evaluator / Triager) — the mocked tests can't catch SDK contract drift, real
  docker permission issues, or `gh` auth surprises.
- **Before a demo** — when you're about to show somebody a live run, run the
  smoke once first so the demo doesn't surface a surprise.

Frequency: not in CI. Run on demand, by hand, when the above conditions hold.

## Prerequisites

| What | Why | Pre-flight check |
|---|---|---|
| `gh` on PATH + logged in | scenario 7 reads PR comments via `gh pr view` | `gh auth status` |
| `docker` on PATH + daemon running | the Executor runs pytest inside a container; scenario 9 stops it on purpose | `docker info` |
| `git` on PATH | scenario 4 reads `git log` on the AIFactory branch | — |
| `python3` + the backend venv | the script uses `apps/backend/.venv/bin/python` for state writes + helpers | `apps/backend/.venv/bin/python --version` |
| **`ANTHROPIC_API_KEY`** in env | every agent calls Claude | `echo $ANTHROPIC_API_KEY \| head -c 7` |
| `PFACTORY_AIFACTORY_ROOT` set | the local AIFactory project checkout the pipeline operates on | the dir must be a git repo with a feature branch |
| `PFACTORY_AIFACTORY_BRANCH` set | the feature branch name | `git -C $PFACTORY_AIFACTORY_ROOT branch --show-current` |
| `PFACTORY_AIFACTORY_PR` set (scenario 7 only) | open PR number to comment on | `gh pr list` in the AIFactory repo |

The script's `--dry-run` mode does NOT require the env vars — useful for
trying the runner before you assemble all the real machinery.

## Quick start

```bash
# List the 9 scenarios
scripts/e2e-smoke.sh --list

# Smoke-test the runner itself (no real env / no LLM calls)
scripts/e2e-smoke.sh --dry-run --all

# Real run, one scenario at a time
export ANTHROPIC_API_KEY=sk-ant-...
export PFACTORY_AIFACTORY_ROOT=$HOME/Source/GitHub/MyApp
export PFACTORY_AIFACTORY_BRANCH=feature/session-expiry
export PFACTORY_AIFACTORY_PR=42

scripts/e2e-smoke.sh --scenario 1     # workspace creation
scripts/e2e-smoke.sh --scenario 2     # portal reachable
# ... and so on
scripts/e2e-smoke.sh --all            # run every scenario in order
```

State + per-scenario outcomes land in `~/.pfactory/e2e-state.json` so a
later `--scenario N` can build on a successful earlier run.

## The 9 scenarios

| # | Name | Mode | What it proves |
|---|---|---|---|
| 1 | workspace_creation | auto | The snapshotter writes `status.json`, `aifactory_spec.md`, `source.json` under `~/.pfactory/workspaces/{proj}/specs/{spec}/`. |
| 2 | portal_starts | auto | Web-server boots on `:3114`; `/api/pfactory/tasks` returns 200. |
| 3 | handover_progression | auto | With `PFACTORY_AUTO_*=1`, status walks `pending → planning → generated → evaluated → triaged` within 5 minutes. |
| 4 | tests_committed | auto | A `pfactory:` commit lands on `$PFACTORY_AIFACTORY_BRANCH` (requires `PFACTORY_TRIAGER_GIT_WRITE=1`). |
| 5 | pytest_passes | auto | `cd $PFACTORY_AIFACTORY_ROOT && pytest tests/` exits 0 with the new tests running. |
| 6 | mutation_kills_test | **manual** | You hand-mutate a line in the changed feature; at least one PFactory-generated test now fails. Proves tests are non-trivial. |
| 7 | pr_comment_posted | auto | `gh pr view --comments` shows the triage report header (requires `PFACTORY_TRIAGER_PR_COMMENT=1` + `PFACTORY_AIFACTORY_PR`). |
| 8 | hallucination_replan | **manual** | Hand-craft a plan with a ghost target; Gen-Functional rejects; Planner replans; no broken test commits. |
| 9 | docker_down_failure | **manual** | Stop docker; trigger a build; status lands at `*_failed` within 2 min, no hang. |

"Auto" scenarios are programmable assertions; "manual" scenarios print
operator instructions and exit with code 77 (skip) — record the outcome
yourself in `~/.pfactory/e2e-state.json`.

## Scenario-by-scenario walkthrough

### Scenario 1: workspace creation

Triggers MCP `task_create_and_run` against the AIFactory project. Expected
side effects:

- `~/.pfactory/workspaces/{proj}/specs/{spec}/status.json` lands with
  `status=pending`
- `context/aifactory_spec.md` is a copy of AIFactory's spec.md
- `context/source.json` records the branch + base_ref + repo path
- `context/diff.patch` records the diff against `base_ref` (if git
  available)

**If it fails:** check `apps/backend/.venv/bin/python -m apps.backend.cli.pfactory_e2e_helper --help`
— the helper module is referenced by scenario 1 and is one of the deferred
items in the phase-2 backlog (see below).

### Scenario 2: portal reachable

Boots the web-server (`apps/web-server`) on port 3114 and polls
`http://localhost:3114/api/pfactory/tasks` until 200 or 30s elapse.

**If it fails:** check `/tmp/pfactory-portal.log` — the script redirects
the server's stdout/stderr there. Common causes: port 3114 already in
use, missing Python deps, `APP_API_TOKEN` not set (defaults to a generated
one in `~/.pfactory/.token`).

### Scenario 3: handover progression

Polls `status.json` for up to 5 minutes. PASS when status reaches `triaged`;
FAIL on any `*_failed` or `stuck` terminal state.

**Common failures:**
- `planner_failed` — Claude API key invalid, or the planner.md prompt's
  schema drifted from `test_plan.json` shape
- `gen_functional_failed` — every subtask hit preflight reject (likely
  a hallucination cascade — check `logs/gen_functional.log`)
- `evaluator_failed` — `verdicts.json` schema invalid (the LLM's verdict
  for one test had `verdict` not in `{accept, reject, flag}`)
- `triager_failed` — usually `verdicts.json` missing (run scenario 1+3 first)

### Scenario 4: tests committed

The triager's git_writer commits accepted + flagged tests onto the
feature branch. Defaults to dry-run. To exercise the real-write path:

```bash
PFACTORY_TRIAGER_GIT_WRITE=1 scripts/e2e-smoke.sh --scenario 4
```

**If it fails:** the dry-run argv log is in `status.json` →
`git_writer.argv_log`. Cherry-pick the failing argv to debug interactively.

### Scenario 5: pytest passes

Just `cd $PFACTORY_AIFACTORY_ROOT && pytest tests/ -q`. Verifies the
generated tests actually parse + run + assert green inside the AIFactory
project's environment.

**If it fails:** look at the FIRST failure — it's often an import error
(preflight slipped a hallucinated import through) or an assertion that
contradicts the feature's real behaviour. Read the test file under
`spec_dir/tests/test_*.py` and trace what the LLM was thinking.

### Scenario 6: mutation kills a test (manual)

The whole *point* of PFactory is to generate tests that catch regressions.
This scenario manually proves it:

1. Identify the feature file changed on `$PFACTORY_AIFACTORY_BRANCH`
   (e.g. `app/auth/login.py`).
2. Make a **small semantic change** to it — flip a boolean, off-by-one,
   remove a guard clause. **Don't** edit the test file.
3. `cd $PFACTORY_AIFACTORY_ROOT && pytest tests/ -q`.
4. **Expect** at least one PFactory-generated test to fail with an
   assertion error pointing at the change you made.
5. `git checkout -- <file>` to restore.

**If no test fails:** the generated tests don't actually exercise the
behaviour they claim. Open `findings/verdicts.json` and look for any
test where the Evaluator's `mutation` signal was `survived` or
`no_mutation` — those are red flags the Evaluator should have caught
upstream.

### Scenario 7: PR comment posted

`gh pr view <N> --comments | grep '# Triage Report'`. Requires both:

```bash
export PFACTORY_AIFACTORY_PR=42
PFACTORY_TRIAGER_PR_COMMENT=1 scripts/e2e-smoke.sh --scenario 7
```

**If it fails:** check `gh auth status` (must be logged in to the
AIFactory repo's GitHub org) and `gh pr view $PFACTORY_AIFACTORY_PR`
(must show the PR you expect).

### Scenario 8: hallucination guard kicks in (manual)

Validates the Planner ↔ Gen-Functional replan loop. Steps:

1. Pick (or create) a fresh spec workspace under `~/.pfactory/workspaces/`.
2. Hand-author its `test_plan.json` with a Lane.FUNCTIONAL subtask whose
   `target` references a **non-existent** symbol — e.g.
   `"app/auth/login.py::ghost_function_that_does_not_exist"`.
3. Set `PFACTORY_AUTO_PLAN=0 PFACTORY_AUTO_GENERATE=1` so the Planner
   doesn't re-emit a fresh plan; only Gen-Functional fires.
4. Trigger Gen-Functional.
5. **Verify** `findings/replan_request.json` is written and status
   transitions to `replan_needed`.
6. Trigger the Planner in replan mode: `python -m agents.planner --mode replan`.
7. **Verify** a new `replan-1` phase is appended to `test_plan.json` and
   the ghost-function subtask's `replan_count == 1`.
8. **Verify** no test file referencing the ghost function exists under
   `tests/`.

### Scenario 9: docker daemon down (manual)

Validates graceful degradation:

1. `sudo systemctl stop docker` (or the equivalent for your runtime).
2. Trigger a fresh `task_create_and_run`.
3. Wait up to 2 minutes.
4. **Verify** `status.json` ends in `*_failed` (not hanging at `*_started`)
   and the `*_error` field carries a docker-related message.
5. `sudo systemctl start docker`.

The Executor (Task 4) wraps the docker invocation with timeouts; this
scenario proves the timeout fires and the Triager's `triager_failed`
path emits a clear message instead of leaving the operator staring at
a half-rendered status.

## Output + state

Each scenario prints:

- A `── Scenario N: name ──` header
- Sub-step lines (`→ description` + `$ command`)
- A final `✓ PASS` / `✗ FAIL` / `⊘ SKIP` summary

State writes to `~/.pfactory/e2e-state.json`:

```json
{
  "started_at": "2026-05-28T16:30:00Z",
  "project_id": "session-expiry-demo",
  "spec_id": "e2e-smoke-1716919800",
  "scenarios": {
    "scenario_1_workspace_creation": {"outcome": "pass", "at": "..."},
    "scenario_2_portal_starts":     {"outcome": "pass", "at": "..."},
    ...
  }
}
```

You can `cat ~/.pfactory/e2e-state.json | jq .` after a run to get a
machine-readable summary.

---

## Phase-2 backlog (sub-task 11.9)

Known sharp edges + deferred work that surfaced during Tasks 5-11.
Track in follow-up issues; not blockers for `v0.1.0-mvp` shipping.

### Helper module gaps

- **`apps.backend.cli.pfactory_e2e_helper` doesn't exist yet.** Scenario 1
  references it as a thin CLI wrapper around MCP `task_create_and_run`.
  Today the operator can invoke the MCP server directly via the portal or
  via `apps/backend/mcp_server/pfactory_server.py`. The helper module
  would be a 30-line wrapper; deferred to Phase 2.

### Deferred trims

- **AIFactory's `runners/github/`** (sub-task 8.4) — ~21,600 lines of
  inherited PR-review machinery. The web-server's `routes/github.py` +
  `services/delegation_tracker.py` + `services/auto_fix_service.py` still
  import from it. A clean trim needs a focused commit after the
  consumers are decoupled.
- **AIFactory's spec-creation routes** (sub-task 9.2) — the inherited
  FastAPI app has `/api/projects/.../tasks` endpoints for AIFactory's
  spec wizard. They don't actively hurt PFactory but they're stale code.
  Same trim-shape as above.
- **Inherited React spec wizard UI** (sub-task 10.2) — 355 TS/TSX files
  inherited. The PFactory portal components live alongside cleanly; a
  later commit can delete the spec-wizard + plan-approval components
  the user no longer needs.

### Pipeline gaps

- **Live log streaming.** The WS endpoint (Task 9 commit 3) sends ONE
  payload on connect — a snapshot of the last 200 lines per file. Live
  tail-as-file-grows would need a `watchdog`-backed loop on the backend.
  Deferred — operators can refresh the tab to see new lines.
- **Per-test coverage XML wiring.** The Evaluator's `coverage_delta`
  primitive is fully tested but the **input** (per-test coverage XML
  files at `findings/runs/<test_id>/coverage.xml`) isn't emitted by the
  Executor yet. As shipped, `coverage_delta` always degrades to "not
  computed" — the LLM still emits verdicts based on the other 4 signals.
  Plumbing: have `DockerRunner.run_pytest` emit one coverage XML per
  test (via `--cov-report=xml:<path>`).
- **`source.json` PR + repo_slug fields.** The Triager's `pr_comment`
  helper needs `source.json["pr_number"]` and (optional) `repo_slug` to
  post comments. The snapshotter (Task 3) doesn't populate these yet —
  operator sets them manually for now.

### Sharp edges

- **Verification field schema drift.** The Planner's `planner.md` prompt
  emits `verification: {"command": "..."}` but the dataclass field is
  `verification.run`. Both shapes are accepted via duck-typing
  (`prompts_pkg/prompts.py`); proper reconciliation deferred.
- **`runners/github/` ⇄ web-server coupling.** See "deferred trims" —
  needs careful surgery.
- **Manual smokes 6/8/9 lack auto-assertions.** They print operator
  steps and skip with code 77. A Phase-2 follow-up could automate them
  with `pytest-fixtures-as-cli` patterns.
- **NixOS + `NODE_ENV=production`** — the nix devShell sets
  `NODE_ENV=production`, which makes `npm install` skip devDependencies
  silently. Captured in Task 10 commit 1 message; documented here too:
  `unset NODE_ENV` before `npm install` in `apps/frontend-web`.

### Known reliability questions

- **Replan loop bound.** If Gen-Functional rejects → Planner replan →
  Gen-Functional rejects → loop continues until `replan_count >= 2`
  (Task 5 commit 5 stuck-at-2 logic). What if the LLM keeps hallucinating
  *different* ghost targets? Each gets a new `replan_count=0`. The
  whole-task budget is implicit (per-subtask × subtask-count); add an
  explicit per-task ceiling in Phase 2.
- **Verdict-from-Evaluator validation gaps.** The validator
  (`_validate_verdicts`) checks the top-level array + verdict-value enum
  + test_id presence. It does NOT enforce that the Evaluator's verdict
  list is a 1:1 superset of the generated tests. A malicious / confused
  agent could omit a verdict and we wouldn't flag it.

### Documentation deltas to chase

- The handover-to-pfactory skill (`.claude/skills/handover-to-pfactory/`)
  predates the four-agent pipeline; its prompt mentions only the
  Planner. Update once `v0.1.0-mvp` is tagged.
- `README.md` Quick Start doesn't mention the e2e smoke; add a "Verify
  your environment" section pointing here.

---

## Troubleshooting

| Symptom | Probable cause | Fix |
|---|---|---|
| `command not found: gh` | Not on PATH in your shell | `which gh` — install via your package manager |
| pre-flight: `python venv not found` | Backend venv not bootstrapped | `cd apps/backend && uv venv && uv pip install -r requirements.txt` |
| Scenario 3 status hangs at `planning` | Planner LLM call too slow / API rate-limited | Tail `logs/planner.log` — check for 429 |
| Scenario 5 fails: ModuleNotFoundError | Generated test imported a missing module | Gen-Functional's preflight missed a hallucination. Investigate via `findings/verdicts.json` + `tests/test_*.py` |
| Scenario 7: `0 comments` | Triager defaulted to dry-run (per CLAUDE.md no-auto-push policy) | Set `PFACTORY_TRIAGER_PR_COMMENT=1` and re-run scenario 3 (the chain) |
| State file getting clobbered between runs | Multiple runs sharing `~/.pfactory/e2e-state.json` | Set `PFACTORY_E2E_STATE_DIR` to a per-run directory |

---

## See also

- [`scripts/e2e-smoke.sh`](../scripts/e2e-smoke.sh) — the runner this guide describes
- [`tests/test_e2e_smoke_script.py`](../tests/test_e2e_smoke_script.py) — structural tests for the runner
- [`guides/planner-manual-smoke.md`](planner-manual-smoke.md) — the Planner-only sibling smoke (predates this guide)
- [`guides/HANDOVER_WORKFLOW.md`](HANDOVER_WORKFLOW.md) — how operators trigger PFactory from a live Claude Code session
- [`guides/aifactory-handback.md`](aifactory-handback.md) — the reverse direction: hand failing tests back to AIFactory for a fix, and the bounded test→fix→re-test loop
