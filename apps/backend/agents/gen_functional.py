"""Gen-Functional agent — Task 6, issue #7 (v0.1) / issue #22 (v0.2).

Second agent in the six-agent PFactory pipeline (Planner ← Gen-Functional →
Executor → Evaluator → Triager). Reads the Planner's emitted
`test_plan.json`, generates test code for each ``Lane.UNIT`` subtask
via the Claude Agent SDK, runs two MVP guardrails per subtask
(pre-flight static check + flake-risk lint), and either commits the
test file or writes a ``context/replan_request.json`` for the Planner.

**v0.2 (Task 6 / #22) additions:**

  - Per-subtask framework lookup via ``framework_registry.get_descriptor``.
  - The framework descriptor is forwarded to
    ``get_pfactory_gen_functional_prompt`` which injects the framework's
    ``context_block`` into the generic prompt body (replacing the
    Python-specific v0.1 prompt for polyglot subtasks).
  - ``_resolve_runner_fn`` now reads the image from
    ``framework_descriptor.runtime.image`` instead of the hardcoded
    ``pfactory-runner-pytest:latest`` constant.
  - v0.1-style subtasks (``subtask.framework is None``) degrade
    gracefully with a ``DeprecationWarning`` on both the prompt helper
    and the runner-image paths.

v0.1 Task 6 commits (all landed):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Pre-flight static check (subprocess introspection)
  ✓ commit 3 — Flake-risk lint (AST patterns)
  ✓ commit 4 — gen_functional.md prompt + assembly helper
  ✓ commit 5 — Real run_gen_functional with SDK + guards + replan_request
  ✓ commit 6 — Integration test + close #7
"""

import asyncio
import json
import logging as _logging
import os
import traceback
from pathlib import Path
from typing import Literal

from agents.agent_infra import (
    BackgroundTaskRegistry,
    iter_subtasks,
    now_iso,
    read_status,
    write_status_patch,
)

_gen_log = _logging.getLogger(__name__)


# ─── Per-agent infra shared via agents.agent_infra (#195) ──────────────────
# These thin module-level wrappers keep the historical private names + the
# "gen_functional" stage discriminator so call sites and tests are unchanged.
_now_iso = now_iso


def _read_status(spec_dir: Path) -> dict:
    return read_status(spec_dir)


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    write_status_patch(spec_dir, "gen_functional", **fields)


# ─── The agent itself ─────────────────────────────────────────────────────

# ─── SDK seams (mockable in tests) ──────────────────────────────────────


async def _resolve_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the generation phase.

    Same pattern as planner._resolve_planner_client — heavy imports
    deferred to runtime so tests can mock this seam without the SDK chain.
    """
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    gen_model = get_phase_model(spec_dir, "coding", None)
    provider_name = infer_provider_from_model(gen_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "coding")
        return create_client(
            project_dir,
            spec_dir,
            gen_model,
            max_thinking_tokens=thinking_budget,
        )
    extra = get_provider_extra_kwargs(provider_name, gen_model)
    # Ollama runs file ops through PFactory's ToolExecutor (sandboxed to
    # working_dir). Generated tests are written into the spec/workspace dir,
    # outside the SUT project — allow it explicitly. Other agentic providers
    # use their own sandboxes and don't take this kwarg.
    if provider_name == "ollama":
        extra["extra_roots"] = [spec_dir]
    return get_provider(
        provider_name,
        phase="coding",
        working_dir=project_dir,
        model=extra.pop("model", gen_model),
        **extra,
    )


async def _invoke_session(
    client,
    prompt: str,
    spec_dir: Path,
    verbose: bool,
) -> tuple[str, str, dict]:
    """Wrap run_agent_session so tests can patch one symbol."""
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client,
            prompt,
            spec_dir,
            verbose,
            phase=LogPhase.CODING,
        )


# ─── Workspace helpers ──────────────────────────────────────────────────


def _files_to_create(subtask) -> list[str]:
    """Subtask.files_to_create may be a list (dataclass) or list-via-dict."""
    f = getattr(subtask, "files_to_create", None)
    if f is None and isinstance(subtask, dict):
        f = subtask.get("files_to_create")
    return list(f or [])


def _write_replan_request(
    spec_dir: Path,
    subtask_id: str,
    reason: str,
    failed_target: str,
) -> None:
    """Write context/replan_request.json for the Planner's replan mode.

    Schema matches what the planner_replan.md prompt + the planner's
    _load_replan_request helper expect: {subtask_id, reason, failed_target}.
    """
    rr = spec_dir / "context" / "replan_request.json"
    rr.parent.mkdir(parents=True, exist_ok=True)
    rr.write_text(
        json.dumps(
            {
                "subtask_id": subtask_id,
                "reason": reason,
                "failed_target": failed_target,
                "rejected_at": _now_iso(),
            },
            indent=2,
        )
    )


def _advance_to_planner_replan(spec_dir: Path, project_dir: Path) -> None:
    """Schedule the Planner in replan mode after a guardrail rejection.

    Lazy import so a circular gen_functional ↔ planner can't form. Same
    GC-anchor pattern as the planner's own _advance_to_gen_functional.
    """
    try:
        from agents.planner import schedule_planner

        schedule_planner(spec_dir, project_dir, mode="replan")
    except ImportError as exc:
        _gen_log.warning(
            "could not auto-schedule planner replan: %s",
            exc,
        )


def _resolve_runner_fn(framework_descriptor=None):
    """Return a runner callable parameterized by the framework's Docker image.

    In v0.2 the runner image is taken from
    ``framework_descriptor.runtime.image``.  For v0.1-style subtasks
    (``framework_descriptor=None``) the legacy default image
    ``pfactory-runner-pytest:latest`` is used and a ``DeprecationWarning``
    is emitted.

    The returned callable has the signature::

        runner_fn(test_file: Path, project_dir: Path, seed: int) -> RunResultLike

    matching the seam that ``stability_runner.check_stability`` and
    ``mutate_probe.mutate_and_probe`` expect.

    Args:
        framework_descriptor: A ``FrameworkDescriptor`` instance, or ``None``
            for v0.1-style subtasks.

    Returns:
        A callable that runs a test file via DockerRunner.

    Note:
        Heavy imports are deferred so tests can patch this function without
        pulling in the full Docker runtime chain.
    """
    import warnings

    # The canonical constant, not a second spelling of it (#493). This carried
    # its own `"pfactory-runner-python:latest"` literal, so the deprecated path
    # had to be found and fixed separately from the one #493 names -- which is
    # exactly how it stayed on the v0.1 name after the rename. Imported as the
    # MODULE constant, not `DockerRunner.DEFAULT_IMAGE`: the tests below replace
    # the class wholesale, and a default read off the replacement is not the
    # default.
    from tools.runners.docker_runner import (
        DEFAULT_RUNNER_IMAGE as _DEFAULT_IMAGE,
        DockerRunner,
    )

    if framework_descriptor is None:
        warnings.warn(
            f"_resolve_runner_fn: framework_descriptor not provided; "
            f"falling back to default image {_DEFAULT_IMAGE!r}. "
            "Pass framework_descriptor for polyglot runner dispatch; "
            "this default will be removed in v0.3.",
            DeprecationWarning,
            stacklevel=2,
        )
        image = _DEFAULT_IMAGE
    else:
        image = (
            getattr(getattr(framework_descriptor, "runtime", None), "image", None) or _DEFAULT_IMAGE
        )

    runner = DockerRunner(image=image)

    def _run(test_file: Path, project_dir_arg: Path, seed: int):
        return runner.run_pytest(
            test_file=test_file,
            project_dir=project_dir_arg,
            seed=seed,
        )

    return _run


def _resolve_framework_descriptor(subtask):
    """Look up the FrameworkDescriptor for this subtask's ``framework`` field.

    For polyglot v0.2 subtasks (``subtask.framework`` is set) the descriptor
    is fetched from the framework registry and returned.

    For v0.1-style subtasks (``subtask.framework is None``) ``None`` is
    returned; the caller (``get_pfactory_gen_functional_prompt``) falls back
    to the legacy Python-specific prompt and emits a ``DeprecationWarning``.

    For subtasks whose ``framework`` value is present but unknown to the
    registry, a ``LookupError`` is raised with a helpful message listing the
    available framework names so the operator can diagnose the mismatch.

    Args:
        subtask: A Subtask dataclass or plain dict.

    Returns:
        A ``FrameworkDescriptor`` instance, or ``None`` for v0.1-style subtasks.

    Raises:
        LookupError: When ``subtask.framework`` is set but not registered.
    """
    if isinstance(subtask, dict):
        framework_name = subtask.get("framework")
    else:
        framework_name = getattr(subtask, "framework", None)

    if framework_name is None:
        # v0.1-style subtask — the prompt helper will warn and use the legacy path.
        return None

    try:
        from framework_registry import load_registry

        registry = load_registry()
        if framework_name not in registry:
            available = sorted(registry.keys())
            raise LookupError(
                f"gen_functional: subtask framework {framework_name!r} is not "
                f"registered in the framework registry. "
                f"Available frameworks: {available}. "
                "Check the frameworks/ directory or the subtask's framework field."
            )
        return registry[framework_name]
    except LookupError:
        raise
    except Exception as exc:
        # Registry unavailable (e.g. frameworks/ dir missing in a test env).
        # Log a warning and fall back to None so the legacy path fires rather
        # than crashing the whole gen_functional run.
        _gen_log.warning(
            "gen_functional: could not load framework registry for %r: %s — "
            "falling back to legacy prompt path",
            framework_name,
            exc,
        )
        return None


def _advance_to_evaluator(spec_dir: Path, project_dir: Path) -> None:
    """Schedule the Evaluator after gen_functional's success path.

    Lazy import — same defensive shape as _advance_to_planner_replan.
    Gated by ``PFACTORY_AUTO_EVALUATE`` (default ON; tests pin off).
    """
    try:
        from agents.evaluator import schedule_evaluator

        schedule_evaluator(spec_dir, project_dir, mode="initial")
    except ImportError as exc:
        _gen_log.warning(
            "could not auto-schedule evaluator: %s",
            exc,
        )


# ─── The agent ──────────────────────────────────────────────────────────


async def run_gen_functional(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Generate a test file for each pending subtask, across all lanes.

    Per-subtask loop:
      1. Build prompt via get_pfactory_gen_functional_prompt
      2. SDK session — the agent uses Write to emit ONE test file
      3. Pre-flight static check on the emitted source (commit 2 module)
      4. Flake-risk lint on the source (commit 3 module)
      5. If both pass → mark subtask completed, accumulate count
         If either rejects → delete file, write context/replan_request.json,
         schedule Planner replan, return False (stops the loop; next
         iteration handles whatever the replan emits)
      6. Session error → mark subtask failed, continue with the next

    Status transitions:
      pending/planned → generating → generated (with tests_generated count)
                                   → generated_empty (0 pending subtasks)
                                   → gen_functional_failed (hard error)
                                   → replan_needed (guardrail rejected;
                                                    Planner replan
                                                    auto-scheduled)
    """
    if not spec_dir.is_dir():
        _gen_log.error("gen_functional: spec_dir %s does not exist", spec_dir)
        return False

    try:
        _write_status_patch(
            spec_dir,
            status="generating",
            phase=f"gen_functional_{mode}_started",
        )

        # 1. Load the plan and find pending Lane.UNIT subtasks.
        from test_plan import ImplementationPlan, SubtaskStatus

        plan_file = spec_dir / "test_plan.json"
        if not plan_file.exists():
            _write_status_patch(
                spec_dir,
                status="gen_functional_failed",
                phase="gen_functional_no_plan",
                gen_functional_error="test_plan.json missing — Planner didn't run?",
            )
            return False

        plan = ImplementationPlan.load(plan_file)
        # Generate every pending subtask regardless of lane — the per-subtask
        # framework descriptor (Playwright / Jest / pytest / httpx) drives the
        # prompt + runner image. Previously gated to Lane.UNIT, which silently
        # dropped the browser/api/integration subtasks the Planner emitted
        # (→ generated_empty).
        pending: list = [
            st for _phase, st in iter_subtasks(plan, lambda st: st.status == SubtaskStatus.PENDING)
        ]

        if not pending:
            _write_status_patch(
                spec_dir,
                status="generated_empty",
                phase="gen_functional_no_pending",
                tests_generated=0,
                gen_functional_warnings=["no pending subtasks to generate"],
            )
            return True

        # 2. Lazy imports for the guards + prompt assembly.
        from agents.flake_risk_lint import flake_risk_lint
        from agents.preflight_static import preflight_check
        from prompts_pkg.prompts import get_pfactory_gen_functional_prompt

        tests_generated = 0
        for subtask in pending:
            files = _files_to_create(subtask)
            if not files:
                subtask.fail("subtask had no files_to_create — Planner emit error")
                continue
            test_path = spec_dir / files[0]

            # 3. Resolve the framework descriptor (v0.2 polyglot path).
            #    For v0.1-style subtasks (framework=None) the descriptor is
            #    None, which triggers the legacy prompt + DeprecationWarning.
            framework_descriptor = _resolve_framework_descriptor(subtask)

            # 4. Run the SDK session for this subtask.
            prompt = get_pfactory_gen_functional_prompt(
                spec_dir,
                project_dir,
                subtask,
                framework_descriptor=framework_descriptor,
            )
            client = await _resolve_client(spec_dir, project_dir)
            session_status, _response, _err = await _invoke_session(
                client,
                prompt,
                spec_dir,
                verbose,
            )
            if session_status == "error":
                _gen_log.warning(
                    "gen_functional: session error on subtask %s — skipping",
                    subtask.id,
                )
                subtask.fail("SDK session returned status=error")
                continue

            # 4. Did the agent actually write the file?
            if not test_path.exists():
                _write_replan_request(
                    spec_dir,
                    subtask_id=subtask.id,
                    reason="agent did not Write the expected test file",
                    failed_target=getattr(subtask, "target", "") or "",
                )
                plan.save(plan_file)
                _write_status_patch(
                    spec_dir,
                    status="replan_needed",
                    phase="gen_functional_no_write",
                    last_rejected_subtask=subtask.id,
                    tests_generated=tests_generated,
                )
                _advance_to_planner_replan(spec_dir, project_dir)
                return False

            source = test_path.read_text()

            # The pre-flight + flake-lint guards parse Python ASTs, so they only
            # apply to Python sources. For TS/JS (Playwright / Jest) and other
            # languages they would false-reject valid tests — skip them.
            # language=None is the v0.1 legacy Python path, so treat it as Python.
            is_python = (subtask.language or "python") == "python"

            # 5. Pre-flight static check (commit 2) — Python only.
            pre = preflight_check(source, project_dir=project_dir) if is_python else None
            if pre is not None and not pre.ok:
                test_path.unlink(missing_ok=True)
                reasons = (
                    ", ".join(f"{f.describe()} — {f.reason[:80]}" for f in pre.failures)
                    or pre.summary()
                )
                _write_replan_request(
                    spec_dir,
                    subtask_id=subtask.id,
                    reason=f"pre-flight rejected: {reasons}",
                    failed_target=getattr(subtask, "target", "") or "",
                )
                plan.save(plan_file)
                _write_status_patch(
                    spec_dir,
                    status="replan_needed",
                    phase="gen_functional_preflight_rejected",
                    last_rejected_subtask=subtask.id,
                    tests_generated=tests_generated,
                )
                _advance_to_planner_replan(spec_dir, project_dir)
                return False

            # 6. Flake-risk lint (commit 3) — Python only (AST-based).
            flake = flake_risk_lint(source) if is_python else None
            if flake is not None and not flake.ok:
                test_path.unlink(missing_ok=True)
                reasons = (
                    "; ".join(f"L{h.lineno} {h.pattern}: {h.detail[:60]}" for h in flake.rejected)
                    or flake.summary()
                )
                _write_replan_request(
                    spec_dir,
                    subtask_id=subtask.id,
                    reason=f"flake-lint rejected: {reasons}",
                    failed_target=getattr(subtask, "target", "") or "",
                )
                plan.save(plan_file)
                _write_status_patch(
                    spec_dir,
                    status="replan_needed",
                    phase="gen_functional_flake_rejected",
                    last_rejected_subtask=subtask.id,
                    tests_generated=tests_generated,
                )
                _advance_to_planner_replan(spec_dir, project_dir)
                return False

            # 7. Both guards passed → mark subtask done.
            subtask.complete(output=f"wrote {files[0]}")
            tests_generated += 1

        plan.save(plan_file)

        if tests_generated == 0:
            _write_status_patch(
                spec_dir,
                status="gen_functional_failed",
                phase="gen_functional_no_tests_generated",
                tests_generated=0,
                gen_functional_error=(
                    "every pending subtask failed (session errors); no "
                    "tests generated and no replan request written"
                ),
            )
            return False

        # #107 (task 5): when a browser subtask authenticates against a
        # ref-auth target, scaffold the storageState login-once setup —
        # auth.setup.ts (logs in once) + a requires_auth Playwright config
        # whose tests depend on the setup project + reuse its session.
        # Best-effort: scaffolding must never fail the generation.
        if any(
            getattr(st, "requires_auth", False)
            and "browser" in str(getattr(st, "lane", "")).lower()
            for st in pending
        ):
            try:
                from agents.evidence import scaffold_auth_setup

                scaffold_auth_setup(spec_dir)
            except Exception:  # noqa: BLE001 - never break generation on scaffolding
                pass

        _write_status_patch(
            spec_dir,
            status="generated",
            phase="gen_functional_complete",
            tests_generated=tests_generated,
        )
        # Forward-chain to the Evaluator (Task 7, #8 — commit 1 lands the
        # scheduler + stub). Gated by ``PFACTORY_AUTO_EVALUATE`` env;
        # tests pin it off to keep this layer deterministic.
        _advance_to_evaluator(spec_dir, project_dir)
        return True

    except Exception as exc:
        _gen_log.error("gen_functional failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="gen_functional_failed",
            phase=f"gen_functional_{mode}_exception",
            gen_functional_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as planner's _BG_PLANNER_TASKS. The planner
# success paths call schedule_gen_functional after writing
# status=planned / planned_empty; gating on env keeps tests
# deterministic.

_BG_GEN_FUNCTIONAL_REGISTRY = BackgroundTaskRegistry()
_BG_GEN_FUNCTIONAL_TASKS: set[asyncio.Task] = _BG_GEN_FUNCTIONAL_REGISTRY.tasks


def schedule_gen_functional(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Gen-Functional, gated by ``PFACTORY_AUTO_GENERATE``.

    Default off in test fixtures (set ``PFACTORY_AUTO_GENERATE=0``);
    production sets ``=1`` so the pipeline auto-advances from Planner
    to Gen-Functional with no manual step.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-generation. Each scheduled task is anchored in
    ``_BG_GEN_FUNCTIONAL_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("PFACTORY_AUTO_GENERATE", "1") == "0":
        return None
    return _BG_GEN_FUNCTIONAL_REGISTRY.schedule(
        run_gen_functional(spec_dir, project_dir, mode=mode),
    )
