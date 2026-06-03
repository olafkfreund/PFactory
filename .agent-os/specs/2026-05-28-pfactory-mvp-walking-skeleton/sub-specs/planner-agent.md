# PFactory Planner Agent — Design Plan

> Task 5 / Issue [#6](https://github.com/olafkfreund/PFactory/issues/6) in the
> PFactory MVP roadmap. First agent in the six-agent pipeline (Planner →
> Generators → Executor → Evaluator → Triager).
>
> Date: 2026-05-28 · Brainstormed via /super-brainstorm

---

## Context

Tasks 1-4 shipped the *substrate*: the hard-fork, the MCP surface, the
workspace + snapshotter, and the Docker runner stack. None of it does
anything yet — `task_create_and_run` produces a workspace with a frozen
copy of the AIFactory spec in `context/` and then idles at
`status=pending`.

The Planner is the agent that turns that frozen snapshot into a
**lane-tagged `test_plan.json`** that the downstream pipeline acts on.
It is the first agent in the chain: nothing else can run until it
emits a plan.

**Why now:** Tasks 6/7/8 (Gen-Functional, Evaluator, Triager) all read
`test_plan.json`. Without a Planner, the pipeline is mute. Issue #6 is
the smallest of the next ready tasks; pairing it with #7 keeps both
under one architectural mental-model.

**Intended outcome:** when this work lands, running
`/handover-to-pfactory` end-to-end produces a workspace with a real,
machine-readable plan emitted by Claude that maps every acceptance
criterion in the AIFactory spec to one or more `Lane.FUNCTIONAL`
subtasks — ready for Gen-Functional to consume.

---

## Locked design decisions (super-brainstorm session)

| # | Decision | Choice |
|---|---|---|
| 1 | **Invocation** | Auto-fire async from `task_create_and_run(confirm=true)`. `status.json` reflects state. Add `--no-auto-plan` env flag (`PFACTORY_AUTO_PLAN=0`) so tests can skip. |
| 2 | **Replan contract** | Stateless. Brand new SDK session per replan. Replan input = original snapshot + current `test_plan.json` + `context/replan_request.json` written by Gen-Functional. Output = ONE corrected subtask appended to a new `replan-N` phase. `replan_count` tracked on the original subtask. After 2 → `status=stuck`. |
| 3 | **Subtask schema** | Extend `apps/backend/test_plan/subtask.py` with three new fields: `target: str \| None`, `rationale: str \| None`, `replan_count: int = 0`. Defaults preserve backward-compat. |
| 4 | **Phase grouping** | One Phase per AIFactory acceptance criterion. `Phase.name` carries the truncated criterion text. Replan phases named `replan-N`. |
| 5 | **Tool grants** | Agent gets `Read`, `Write`, `Glob`, `Grep`. NOT `Bash` (no shell execution). NOT `Edit` (writes are single-file). Allowed paths: read = `spec_dir/` + project's `root_path` (read-only); write = `spec_dir/test_plan.json` only. |
| 6 | **Subtask budget** | Hard cap = 30 subtasks. Soft warning at 15 (recorded in `source.json` post-emit). Beyond cap → planner instructed to prioritize by AC severity; overflow logged. |
| 7 | **Multi-language** | At MVP only Python files in the diff result in functional subtasks. If the diff spans other languages, the planner records a warning in `source.json` (`languages_skipped: [...]`) and proceeds with whatever's in scope. |
| 8 | **Provider / model** | Reuse the existing `get_provider(provider_name, phase="planning", model=None, ...)` factory verbatim. Picks up `get_phase_model("planning")` from existing config. No new provider code. |
| 9 | **Logging** | Full SDK session transcript to `spec_dir/logs/planner.log`. Decisions + warnings also surfaced in `status.json` and `source.json` post-emit. |
| 10 | **Failure handling** | LLM emits no `test_plan.json` → 1 retry with a "you must use the Write tool" reminder turn, then `status=planner_failed`. JSON parse error → 1 retry with the parse error in the prompt, then fail. Empty plan (0 subtasks) → `status=planned_empty` (warning, not failure). |

---

## Architecture

### Pipeline position

```
task_create_and_run(confirm=true)
  └─► snapshot AIFactory spec into context/
  └─► asyncio.create_task(run_planner(spec_dir, project_dir, mode='initial'))
            │
            ▼
    ┌───────────────────────────────────┐
    │ run_planner()                     │
    │ ─────────────────────────────────│
    │ • status.json: status=planning   │
    │ • build prompt (planner.md +     │
    │     spec context + tool grants)  │
    │ • get_provider(phase="planning") │
    │ • run_agent_session(...)         │
    │ • agent uses Write tool to emit  │
    │     test_plan.json directly      │
    │ • post-session: load + validate  │
    │ • status.json: status=planned    │
    │     (or planner_failed / empty)  │
    └───────────────────────────────────┘
            │
            ▼
       waits for Task 6 Gen-Functional
```

### Replan path

```
Gen-Functional rejects subtask 'login-test-bcrypt'
  └─► writes context/replan_request.json:
        { subtask_id, reason, failed_target }
  └─► (Task 6 or Executor) calls
        run_planner(spec_dir, project_dir, mode='replan')
            │
            ▼
      brand new SDK session, prompt includes:
        - context/aifactory_spec.md (full)
        - context/diff.patch         (full)
        - test_plan.json             (current)
        - context/replan_request.json
        - planner_replan.md system prompt
            │
            ▼
      agent emits ONE corrected subtask
        appended to new Phase 'replan-N'
        original subtask.replan_count++
            │
            ▼
      if replan_count >= 2 → mark status=stuck
        original subtask remains, omitted from
        Triager's commit phase
```

### File-system layout (post-Task-5)

```
apps/backend/
├── agents/
│   ├── planner.py                ← REWRITE: add run_planner(); keep run_followup_planner() for legacy
│   └── __init__.py               ← re-export run_planner
├── prompts/
│   ├── planner.md                ← NEW: test-oriented system prompt (initial mode)
│   └── planner_replan.md         ← NEW: replan-mode system prompt
├── prompts_pkg/
│   └── prompts.py                ← extend get_planner_prompt() OR add get_pfactory_planner_prompt()
├── test_plan/
│   └── subtask.py                ← add target / rationale / replan_count fields
└── agents/tools_pkg/tools/
    └── task_control.py           ← task_create_and_run spawns asyncio.create_task(run_planner(...))

tests/
├── test_test_plan_subtask_fields.py  ← NEW: round-trip + defaults for new fields
└── test_planner.py                   ← NEW: mocked-SDK tests for run_planner + replan

~/.pfactory/workspaces/{project_id}/specs/{spec_id}/
├── status.json                   ← lifecycle: pending → planning → planned/failed/empty
├── test_plan.json                ← emitted by the Planner agent's Write tool
├── context/
│   ├── source.json               ← snapshot warnings + (new) planner_warnings
│   └── replan_request.json       ← written by Gen-Functional; consumed in replan mode
└── logs/
    └── planner.log               ← full SDK session transcript
```

---

## Component-level detail

### `run_planner(spec_dir, project_dir, mode='initial', verbose=False) -> bool`

Mirrors the existing `run_followup_planner` shape (`apps/backend/agents/planner.py` lines 41-202).

```python
async def run_planner(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal['initial', 'replan'] = 'initial',
    verbose: bool = False,
) -> bool:
    """Run the PFactory Planner agent.

    Args:
        spec_dir: PFactory workspace spec dir
            (~/.pfactory/workspaces/{pid}/specs/{sid}/).
        project_dir: AIFactory project root_path (for Glob/Grep tools).
        mode: 'initial' for first plan, 'replan' for follow-up after
            Gen-Functional rejection.
        verbose: forwarded to run_agent_session.

    Returns:
        True if a non-empty plan landed and validates.

    Side effects:
        - Updates status.json (status, phase, planner_warnings)
        - Emits / appends test_plan.json
        - Writes spec_dir/logs/planner.log
    """
```

Implementation skeleton:

1. Update `status.json` → `status=planning, phase=planner_started`.
2. Resolve `model = get_phase_model("planning")`; provider via factory.
3. Build prompt:
   - `initial` mode → `get_pfactory_planner_prompt(spec_dir, project_dir)` reads `prompts/planner.md`, prepends a SPEC CONTEXT block (paths, schema reminder, tool grants).
   - `replan` mode → `get_pfactory_planner_replan_prompt(spec_dir)` reads `prompts/planner_replan.md`, prepends a REPLAN CONTEXT block (current plan + replan_request.json contents).
4. Configure SDK client with allowed tools `{Read, Write, Glob, Grep}` and `cwd=spec_dir` (project_dir is on `allowed_paths` for read-only).
5. `status, response, err = await run_agent_session(client, prompt, spec_dir, verbose, phase=LogPhase.PLANNING)`.
6. Post-session: 
   - Try `ImplementationPlan.load(spec_dir / "test_plan.json")`.
   - On FileNotFoundError → retry once (`mode + reminder_turn`); on second failure → `status=planner_failed` + return False.
   - On JSONDecodeError → retry once with the parse error in the prompt; on second failure → `planner_failed` + return False.
   - On empty plan (`phases=[]` or no pending subtasks) → `status=planned_empty` + return True (warning).
   - On valid plan → enforce hard cap (30 subtasks); record `subtask_count` warning in `source.json` if > 15.
7. For `replan` mode: also bump `replan_count` on the original subtask in `test_plan.json`, set its `status=stuck` if `replan_count >= 2`.
8. `status.json` → `status=planned`.

### Subtask schema extension

Three new fields added to `apps/backend/test_plan/subtask.py`:

```python
@dataclass
class Subtask:
    # ... existing fields ...
    lane: Lane = Lane.FUNCTIONAL          # Task 3

    # NEW in Task 5
    target: str | None = None             # e.g., "apps/auth/login.py::login_user"
    rationale: str | None = None          # e.g., "AC#3: rejects expired tokens"
    replan_count: int = 0                 # incremented by replan mode; >= 2 → stuck
```

- `to_dict()` always emits the three new keys.
- `from_dict()` tolerates missing keys (legacy plans round-trip).
- Verification field stays `Verification | None` and is set by the Planner to
  `Verification(type=COMMAND, command=f"pytest {test_file_path}", expected="exit 0")`.

### Prompt structure

`prompts/planner.md` (initial mode, ≤ 8KB target):

```
# PFactory Planner — initial mode

You are PFactory's Planner agent. You read a frozen snapshot of an
AIFactory spec and emit a lane-tagged test_plan.json.

## Output contract
Use the Write tool to create ONE file at:
  {spec_dir}/test_plan.json

The file must validate against the ImplementationPlan schema. ...

## Rules
- Lane: every subtask gets lane="functional" at MVP.
- Phase per acceptance criterion. Phase.name = the criterion text.
- Max 30 subtasks total. Prefer breadth (cover every AC) over depth.
- target: "<repo-relative path>::<symbol>"
- rationale: which AC this subtask covers
- verification: Verification(type=COMMAND, command="pytest <test_path>")
- Skip files whose language isn't in the current lane's supported set
  (lang_registry says MVP = python only).

## What you have
- {spec_dir}/context/aifactory_spec.md — the frozen spec
- {spec_dir}/context/aifactory_plan.json — AIFactory's plan if present
- {spec_dir}/context/diff.patch — base_ref..branch diff
- {spec_dir}/context/source.json — snapshot metadata + warnings
- Project tree at {project_dir} — read-only via Glob / Grep

## Tools
- Read, Write, Glob, Grep
- NO Bash. NO Edit. NO network.
```

`prompts/planner_replan.md` (replan mode, ≤ 4KB target):

```
# PFactory Planner — replan mode

Gen-Functional rejected a subtask. Read context/replan_request.json
for the reason. Emit ONE corrected subtask appended to test_plan.json
as a new Phase named "replan-{N+1}".

Rules carry over from planner.md. Use the existing test_plan.json
as the source of truth — DO NOT rewrite earlier phases.
```

### Tool grants (Claude Agent SDK options)

```python
ClaudeAgentOptions(
    cwd=spec_dir,
    allowed_paths=[spec_dir, project_dir],  # project_dir is implicitly read-only
                                            # (only Read/Glob/Grep target it)
    allowed_tools={"Read", "Write", "Glob", "Grep"},
    permission_mode="bypassPermissions",
    model=resolved_model,
    ...
)
```

The SDK doesn't have a "read-only path" concept; we enforce via the tool
allowlist (no Edit/Bash) + a post-session check that Write was only used
inside `spec_dir`. Verified by Read + Glob being read-only and Write
attempts on `project_dir` being rejected at the SDK layer with the path
prefix check we'll add to a tiny `_post_session_write_audit` helper.

### Status transitions

```
status: pending                 ← task_create_and_run + snapshot OK
        │
        ▼
status: planning                ← run_planner started
        │
   ┌────┼─────┬─────────┬────────┐
   ▼    ▼     ▼         ▼        ▼
planned planned_empty   planner_failed  (replan_loop)
            ↑              ↑              │
            │ warning      │ hard error   ▼
            │ no subtasks  │ retried once status: stuck
            │              │              (subtask-level, not task-level)
```

### Failure-mode matrix (full)

| Symptom | Detection | Recovery |
|---|---|---|
| Agent didn't call Write | `test_plan.json` missing after session | Retry session 1× with reminder turn; second fail → `planner_failed` |
| Wrote invalid JSON | `ImplementationPlan.load()` raises | Retry 1× with parse error in prompt; second fail → `planner_failed` |
| Wrote 0 subtasks | `plan.phases == []` after load | `planned_empty` (warning, return True; Triager decides) |
| > 30 subtasks emitted | post-load count | Truncate to first 30; warning in `source.json` |
| Spec.md missing | `source.json.has_spec_md == False` | Proceed; agent works from diff alone (degraded) |
| Diff missing | `source.json.has_diff_patch == False` | Proceed; agent works from spec alone |
| Spec mentions symbols not in project | Agent uses Glob/Grep, doesn't find them | Subtask emitted with `rationale` noting the suspicion; Gen-Functional's pre-flight catches |
| Diff has non-Python files | language detection on diff hunks | Skip non-Python; record `languages_skipped` in `source.json` |
| Provider quota / 5xx | `run_agent_session` raises | Propagate; status=planner_failed; transcript shows the error |
| Bash/Edit tool called by mistake | shouldn't happen given allowlist | Filtered by SDK; if leaked, post-session audit raises |

---

## Files to create or modify

### New

- `apps/backend/agents/planner.py` — add `run_planner()` (alongside existing `run_followup_planner`)
- `apps/backend/prompts/planner.md` — new test-oriented system prompt
- `apps/backend/prompts/planner_replan.md` — replan mode system prompt
- `tests/test_planner.py` — mocked-SDK tests:
  - initial mode happy path emits valid plan
  - JSON parse error retried once
  - empty plan → `planned_empty`
  - over-budget plan truncated
  - replan mode appends to new `replan-N` phase
  - `replan_count >= 2` marks subtask stuck
  - tools allowlist enforced
  - status.json transitions captured
- `tests/test_test_plan_subtask_fields.py` — Subtask schema additions

### Modified

- `apps/backend/test_plan/subtask.py` — add `target`, `rationale`, `replan_count` fields + update `to_dict` / `from_dict`
- `apps/backend/agents/__init__.py` — export `run_planner`
- `apps/backend/prompts_pkg/prompts.py` — add `get_pfactory_planner_prompt(spec_dir, project_dir)` and `get_pfactory_planner_replan_prompt(spec_dir)`
- `apps/backend/agents/tools_pkg/tools/task_control.py` — in `task_create_and_run` happy path, `asyncio.create_task(run_planner(spec_dir, root_path))` after snapshot succeeds (gated by `os.environ.get("PFACTORY_AUTO_PLAN", "1") != "0"`)
- `scripts/verify-fork.sh` — allowlist additions for the new planner prompts + tests

### Reused as-is

- `apps/backend/agents/session.py` — `run_agent_session()` + `post_session_processing()` unchanged
- `apps/backend/agents/base.py` — constants
- `apps/backend/providers/factory.py` — `get_provider(phase="planning")` unchanged
- `apps/backend/test_plan/plan.py` — `ImplementationPlan.save/.load/.add_followup_phase` unchanged
- `apps/backend/workspaces/snapshotter.py` — Task 3's snapshotter unchanged
- `apps/backend/agents/utils.py` — `load_test_plan`, `sync_plan_to_source` unchanged

---

## Verification plan

1. **Subtask field round-trip** — `pytest tests/test_test_plan_subtask_fields.py` covers default → set → to_dict → from_dict → equal across all combinations of the three new fields. Includes legacy-JSON read (no new keys) test.
2. **Planner initial mode (mocked SDK)** — `tests/test_planner.py::test_initial_emits_plan`. Mocks `ClaudeSDKClient` so the SDK call is replaced with a fixture that writes a canned `test_plan.json`. Verifies status.json transitions, plan validates, subtask cap not tripped.
3. **Planner JSON-retry path** — `test_initial_retries_on_invalid_json`. First call writes invalid JSON, second writes valid. Verifies retry counter, final status=planned.
4. **Planner empty-plan path** — `test_initial_empty_plan_is_warning`. Mock writes empty plan; expect `status=planned_empty, return True`.
5. **Planner over-budget** — `test_initial_over_30_subtasks_truncated`. Mock emits 35 subtasks; expect truncation to 30 + warning in `source.json`.
6. **Replan mode** — `test_replan_appends_phase_and_bumps_count`. Build fixture spec_dir with existing test_plan.json + replan_request.json; call `run_planner(mode='replan')`; verify a new `replan-1` phase, original subtask's `replan_count=1`.
7. **Replan stuck** — `test_replan_count_2_marks_stuck`. Pre-set `replan_count=1`; trigger replan; expect `replan_count=2, status=stuck`.
8. **Auto-fire integration** — `tests/test_pfactory_mcp_tools.py::test_task_create_and_run_kicks_planner` (extend existing). With `PFACTORY_AUTO_PLAN=1`, confirm an asyncio task is scheduled after task_create_and_run; with `PFACTORY_AUTO_PLAN=0`, no task scheduled.
9. **Manual smoke (after Tasks 6-8 also land)** — `/handover-to-pfactory` from an AIFactory project; poll `mcp__pfactory__task_status` until `status=planned`; cat `test_plan.json`; eyeball the phases/subtasks.
10. **Tool-allowlist guard** — `test_planner_rejects_bash_writes`. Inject an SDK message that tries to call Bash; expect SDK-level rejection.

---

## Risks + mitigations

| Risk | Mitigation in this design |
|---|---|
| LLM hallucinates targets — symbols that don't exist | Planner has Glob/Grep; encouraged in prompt to verify before emitting target. Gen-Functional's pre-flight (Task 6) catches anything that slips through; replan handles the recovery. |
| LLM emits > 30 subtasks | Hard truncation post-emit + warning. Planner prompt explicitly states the cap. |
| Status.json race condition (parallel tasks on same workspace) | Each task has its own `{spec_id}/` dir; no shared writes. `status.json` writes are atomic single-file writes. |
| Replan loop runs forever | `replan_count >= 2` gates stuck. Total replan calls per task capped by per-subtask budget × subtask count. |
| Spec is huge (token budget blown) | Prompt instructs the agent to focus on diff lines first; full spec is provided but the agent is told to skim. Hard cap on subtasks bounds the output. Open follow-up: if prompts overflow, we may need to chunk the spec — but realistic AIFactory specs are <10KB so this isn't expected at MVP. |
| Provider is misconfigured / API key missing | Errors surface in `status=planner_failed` + log transcript. Operator sees the issue in the portal (Task 9). |
| Auto-fire silently dies (asyncio task fire-and-forget) | The `asyncio.create_task` is wrapped in an outer try/except that writes status=planner_failed on uncaught exceptions. Plus the task is tracked in a module-level set so it doesn't get GC'd. |
| Conflict with `run_followup_planner` namespace | We keep the followup function intact (still callable for AIFactory legacy if anyone wants it) but `agents/__init__.py` only exports `run_planner`. The existing prompt `planner.md` is RENAMED to `planner_legacy.md` since the inherited one is feature-development-oriented; the new test-oriented prompt takes the `planner.md` name. |

---

## Implementation order (for execute-tasks)

1. **Subtask schema** (smallest, isolated). New fields + tests. Lands as commit #1.
2. **Auto-fire scaffold** in `task_create_and_run` with a STUB `run_planner` that just writes status=planned + an empty plan. Tests confirm the scheduler fires. Lands as commit #2.
3. **`prompts/planner.md` + `planner_replan.md`** authoring. Lands as commit #3.
4. **Real `run_planner`** wiring the agent session, post-session validation, retry logic. Lands as commit #4.
5. **Replan path** + replan_count bookkeeping + stuck transition. Lands as commit #5.
6. **Integration test + manual smoke prep**. Lands as commit #6.

All six tracked under issue #6; closed once Task 6 (Gen-Functional, #7)
verifies the plan is consumable.

---

## Next steps after spec approval

Three options for moving from spec → code:

- **A) `/execute-tasks`** — break this plan into a `.agent-os/specs/.../tasks.md` task list and execute. Most aligned with the user's Agent OS workflow.
- **B) `/superhuman`** — decompose + parallel waves. Heavier; useful if multiple sub-tasks are independent (they're not for the Planner — order matters).
- **C) Direct implementation** — start with commit 1 of the implementation order above and iterate.

Recommendation: **A** if you want the spec to live in `.agent-os/specs/` properly, **C** if you'd rather just press on and let the implementation-order checklist above be the task list.
