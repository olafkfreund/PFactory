"""Evaluator agent — Task 7, issue #8.

Third agent in the six-agent PFactory pipeline:

    Planner → Gen-Functional → Executor → Evaluator → Triager

Reads completed Lane.UNIT subtasks from test_plan.json, computes
five evaluation signals per generated test (coverage delta, 3× stability,
mutate-and-check, lint promotion + the LLM's semantic-relevance call),
hands them to an LLM via the evaluator.md prompt, then validates the
verdicts.json the LLM writes.

Task 7 commits (all landed):

  ✓ commit 1 — Auto-fire scaffold + stub
  ✓ commit 2 — Coverage-delta + 3× stability re-run primitives
  ✓ commit 3 — Mutate-and-check probe + flake-lint promotion primitives
  ✓ commit 4 — evaluator.md prompt + assembly helper
  ✓ commit 5 — Real run_evaluator with SDK + 5 signals → verdicts.json
  ✓ commit 6 — Integration test + close #8

Task 8 additions (Browser-lane AppRuntime status transitions):

  The Evaluator now surfaces two Browser-lane phases in status.json so
  the portal's LaneStatusGrid can show operators what is happening:

    ``executor_app_running``  — docker-compose services are up + healthy;
                                the Playwright container is executing.
    ``app_not_healthy``       — the AppRuntime health-poll timed out before
                                all ``wait_for`` URLs replied with their
                                expected HTTP status code.  The error
                                message includes the last observed status
                                code per URL.

  These phases are set by ``_run_browser_subtask_with_runtime()`` which
  wraps the AppRuntime + DockerRunner lifecycle for a single Browser-lane
  subtask.  ``run_evaluator`` calls this helper instead of the plain
  DockerRunner path when the subtask's lane is ``"browser"`` or
  ``"integration"``.

  Implementation note: the status transitions are thin wrappers — the
  heavy lifting (AppRuntime lifecycle, health-poll, PFACTORY_TARGET_URL
  injection) lives in ``tools/runners/app_runtime.py`` and
  ``tools/runners/lane_dispatch.py``.  The Evaluator only owns the
  *status.json* side-effects.

Lane pipeline (issue #193):

  The per-lane evaluation logic (subtask selection, runner_fn construction,
  signal-bundle assembly) lives in the ``agents.lanes`` package — one module
  per lane (unit/api/browser/jest) plus shared ``common`` + ``signals``
  modules.  This file is the thin orchestrator: it resolves the SDK seams,
  fans completed subtasks out to the lane builders, invokes the LLM, and
  validates verdicts.json.  The symbols below are re-exported for backward
  compatibility (tests and ``gen_functional`` import them from here).
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Literal

# ── Lane pipeline (issue #193) — extracted helpers, re-exported here so the
#    existing public import surface (``agents.evaluator.<symbol>``) keeps working.
from agents.lanes.api import _build_api_signal_bundle, _completed_api_subtasks
from agents.lanes.browser import (
    _build_browser_signal_bundle,
    _completed_browser_subtasks,
    _resolve_browser_runner_fn,
    _run_browser_subtask_with_runtime,
    _stage_browser_test,
)
from agents.lanes.common import (
    _PYTEST_IMAGE,
    _browser_target_url,
    _kube_runtime_for,
    _now_iso,
    _resolve_target,
    _RunResultLike,
    _test_credential_specs,
    _write_status_patch,
)
from agents.lanes.jest import (
    _build_jest_signal_bundle,
    _completed_jest_subtasks,
    _resolve_jest_runner_fn,
)
from agents.lanes.signals import (
    EvaluatorSignals,
    _coverage_delta_for_subtask,
    _flaky_history_for_subtask,  # noqa: F401 — re-export for back-compat
    _framework_coverage_strategy,
    _lint_promotion_for_subtask,  # noqa: F401 — re-export for back-compat
    _mutation_for_subtask,  # noqa: F401 — re-export for back-compat
    _stability_for_subtask,  # noqa: F401 — re-export for back-compat
)
from agents.lanes.unit import _build_signal_bundle, _completed_functional_subtasks

__all__ = [
    "EvaluatorSignals",
    "_browser_target_url",
    "_build_api_signal_bundle",
    "_build_browser_signal_bundle",
    "_build_jest_signal_bundle",
    "_build_signal_bundle",
    "_completed_api_subtasks",
    "_completed_browser_subtasks",
    "_completed_functional_subtasks",
    "_completed_jest_subtasks",
    "_coverage_delta_for_subtask",
    "_framework_coverage_strategy",
    "_kube_runtime_for",
    "_resolve_browser_runner_fn",
    "_resolve_jest_runner_fn",
    "_resolve_target",
    "_run_browser_subtask_with_runtime",
    "_stage_browser_test",
    "_test_credential_specs",
    "_validate_verdicts",
    "run_evaluator",
    "schedule_evaluator",
]

_eval_log = _logging.getLogger(__name__)


# ─── SDK seams (mockable in tests) ──────────────────────────────────────


async def _resolve_evaluator_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the evaluation phase.

    Same pattern as ``planner._resolve_planner_client`` /
    ``gen_functional._resolve_client``. Heavy imports deferred to
    runtime so tests can mock this seam without the SDK chain.

    Uses the 'coding' phase model for now — same budget as
    Gen-Functional. A 'evaluation' phase can be added to phase_config
    once we know the right thinking-token budget. Conservative for now.
    """
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    eval_model = get_phase_model(spec_dir, "coding", None)
    provider_name = infer_provider_from_model(eval_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "coding")
        return create_client(
            project_dir,
            spec_dir,
            eval_model,
            max_thinking_tokens=thinking_budget,
        )
    extra = get_provider_extra_kwargs(provider_name, eval_model)
    # Ollama runs file ops through PFactory's ToolExecutor (sandboxed to
    # working_dir); the Evaluator reads/writes within the spec/workspace dir,
    # outside the SUT project — allow it explicitly. Other agentic providers
    # use their own sandboxes and don't take this kwarg.
    if provider_name == "ollama":
        extra["extra_roots"] = [spec_dir]
    return get_provider(
        provider_name,
        phase="coding",
        working_dir=project_dir,
        model=extra.pop("model", eval_model),
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


# ─── Runner-fn seam for stability + mutation primitives (pytest lanes) ───


def _resolve_runner_fn(
    spec_dir: Path,
    project_dir: Path,
    image: str = _PYTEST_IMAGE,
    network: str = "none",
    target_url: str | None = None,
    subtask: dict | None = None,
) -> Callable[[Path, Path, int], _RunResultLike]:
    """Return a callable matching the runner_fn seam for the pytest-based lanes
    (unit + api).

    ``runner_fn(test_file, project_dir, seed) -> DockerRunResult``. The given
    ``test_file`` may be the generated test (under the workspace) OR a mutated
    copy (under ``findings/mutants/``) — the runner copies the SUT and the
    specific test into a writable scratch dir on the host, then runs pytest
    inside it so ``from <module> import ...`` resolves (pyproject pythonpath).

    ``network``/``target_url`` support the **api** lane: pass ``network="host"``
    so the in-container test can reach a host-served app, and ``target_url`` to
    inject ``PFACTORY_TARGET_URL`` (httpx tests read it). The unit lane uses the
    defaults (``network="none"``, no target URL) for hermetic execution.

    Tests mock this whole function so the stability + mutation primitives can be
    exercised without Docker.
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        scratch = Path(_tmp.mkdtemp(prefix="tf-pytest-"))
        try:
            # Host-side staging: copy the SUT, then drop the specific test file
            # under tests/. Doing this on the host (scratch is bind-mounted rw)
            # sidesteps the read-only /work mount + container-uid write issues.
            for item in Path(project_dir_arg).iterdir():
                if item.name == ".git":
                    continue
                dst = scratch / item.name
                if item.is_dir():
                    _sh.copytree(item, dst, dirs_exist_ok=True)
                else:
                    _sh.copy2(item, dst)
            tdir = scratch / "tests"
            tdir.mkdir(exist_ok=True)
            _sh.copy2(test_file, tdir / Path(test_file).name)
            # The container runs as a non-root uid; make scratch world-writable.
            for p in scratch.rglob("*"):
                try:
                    p.chmod(0o777)
                except OSError:
                    pass
            scratch.chmod(0o777)

            runner = DockerRunner(image=image, network=network, read_only_rootfs=False)
            cmd = (
                "cd /scratch && "
                f"python -m pytest tests/{Path(test_file).name} "
                "-p no:cacheprovider -q --junitxml=/scratch/junit.xml "
                "--cov-report=xml:/scratch/coverage.xml --cov=. 2>&1; "
                "echo __PYTEST_EXIT=$?"
            )
            extra_env = {"PYTHONHASHSEED": str(seed)}
            if target_url:
                extra_env["PFACTORY_TARGET_URL"] = target_url
                extra_env["APP_URL"] = target_url
            # Sandbox credential injection (#73): only the network-enabled api
            # lane (network != "none") gets broker-resolved creds, and only
            # when egress is opted in. Unit lane (network="none") gets neither.
            from tools.runners.sandbox_credentials import resolve_sandbox_credentials

            sandbox_creds = resolve_sandbox_credentials(project_dir_arg, spec_dir, network)
            extra_env.update(sandbox_creds.env)
            # Test-target login credentials (#107): a ref-auth target's
            # username/secret, resolved + injected as env (egress-gated like #73).
            from tools.runners.sandbox_credentials import (
                resolve_test_target_credentials,
            )

            test_creds = resolve_test_target_credentials(
                _test_credential_specs(spec_dir, subtask),
                project_dir_arg,
                spec_dir,
                network,
            )
            extra_env.update(test_creds.env)
            try:
                res = runner.run(
                    repo_path=Path(project_dir_arg).resolve(),
                    scratch_path=scratch.resolve(),
                    command=["sh", "-c", cmd],
                    extra_env=extra_env,
                    secret_files=sandbox_creds.files,
                    timeout_sec=300,
                )
            finally:
                sandbox_creds.wipe()  # erase materialised secret files
                test_creds.wipe()
            code = res.returncode
            for line in (res.stdout or "").splitlines():
                if line.startswith("__PYTEST_EXIT="):
                    try:
                        code = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
            junit = scratch / "junit.xml"
            cov = scratch / "coverage.xml"
            return DockerRunResult(
                returncode=code,
                stdout=res.stdout,
                stderr=res.stderr,
                junit_xml_path=junit if junit.exists() else None,
                coverage_xml_path=cov if cov.exists() else None,
                argv=res.argv,
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


# ─── Verdicts.json validation ───────────────────────────────────────────


_VALID_VERDICTS = frozenset({"accept", "reject", "flag"})


def _loads_tolerant(text: str) -> tuple[object, bool]:
    """Parse JSON that may carry a markdown fence or trailing prose.

    Thin wrapper over the shared agent-output envelope (#96): strict parse,
    then fence-strip / first-value ``raw_decode`` / outermost brace-match.

    Returns ``(doc, salvaged)`` — ``salvaged`` is True when the lenient path
    recovered the object, so the caller can rewrite a clean file. Raises
    ``json.JSONDecodeError`` when no JSON object can be recovered (preserved
    for the existing caller).
    """
    from agents.output_envelope import OutputEnvelopeError, extract_json

    try:
        return extract_json(text)
    except OutputEnvelopeError as exc:
        raise json.JSONDecodeError(str(exc), text or "", 0) from None


def _validate_verdicts(
    path: Path,
    skip_coverage_test_ids: frozenset[str] | None = None,
) -> tuple[bool, str, int]:
    """Validate the agent's verdicts.json.

    Args:
        path: Path to the verdicts.json file to validate.
        skip_coverage_test_ids: Optional set of test IDs whose framework has
            ``coverage_strategy == "skip"``.  When provided, a numeric
            ``signals_summary.coverage_delta_pct`` on one of these tests
            triggers a WARNING (the LLM should have left it null) but the
            verdict is still **accepted** — we don't reject a verdict over a
            cosmetic mismatch.

    Returns:
        (ok, error_message, verdicts_count).
        On success: (True, "", N). On failure: (False, "reason", 0).

    Accepted values for ``signals_summary.coverage_delta_pct``:
        - ``null`` / Python ``None`` — browser lane or coverage not computed.
        - Any ``int`` or ``float`` — numeric coverage delta percentage.
        - Key absent entirely — backward-compat; treated as null.

    Rejected values:
        - A string (e.g. ``"12.3"`` or ``"N/A"``).
        - Any other non-numeric type.
    """
    _skip_ids: frozenset[str] = skip_coverage_test_ids or frozenset()

    if not path.exists():
        return False, "verdicts.json not written by agent", 0
    try:
        doc, salvaged = _loads_tolerant(path.read_text())
    except json.JSONDecodeError as exc:
        return False, f"verdicts.json is not valid JSON: {exc}", 0
    if not isinstance(doc, dict):
        return False, "verdicts.json root is not an object", 0
    if salvaged:
        # The agent wrapped the JSON in a fence or appended trailing prose.
        # Rewrite the canonical object so the Triager (which json.loads the
        # same file) doesn't trip over it.
        _eval_log.warning(
            "verdicts.json had extra data around the JSON; rewrote the salvaged object."
        )
        try:
            path.write_text(json.dumps(doc, indent=2))
        except OSError:
            pass
    verdicts = doc.get("verdicts")
    if not isinstance(verdicts, list):
        return False, "verdicts.json missing 'verdicts' array", 0
    for i, v in enumerate(verdicts):
        if not isinstance(v, dict):
            return False, f"verdict[{i}] is not an object", 0
        if "test_id" not in v:
            return False, f"verdict[{i}] missing 'test_id'", 0
        if v.get("verdict") not in _VALID_VERDICTS:
            return (
                False,
                (
                    f"verdict[{i}] has invalid 'verdict': "
                    f"{v.get('verdict')!r} (must be one of {sorted(_VALID_VERDICTS)})"
                ),
                0,
            )
        # Validate signals_summary.coverage_delta_pct when present.
        # Accepted: null (None) or a numeric value (int/float).
        # Rejected: a string (the LLM must not emit "12.3" or "N/A" as text).
        signals = v.get("signals_summary")
        if isinstance(signals, dict) and "coverage_delta_pct" in signals:
            cdp = signals["coverage_delta_pct"]
            if cdp is not None and not isinstance(cdp, (int, float)):
                return (
                    False,
                    (
                        f"verdict[{i}].signals_summary.coverage_delta_pct "
                        f"must be a number or null, got {cdp!r}"
                    ),
                    0,
                )
            # Warn if the LLM emitted a numeric value for a skip-coverage test.
            test_id = v.get("test_id", "")
            if test_id in _skip_ids and isinstance(cdp, (int, float)):
                _eval_log.warning(
                    "verdict[%d] test_id=%r is on a skip-coverage framework "
                    "but signals_summary.coverage_delta_pct=%r is numeric; "
                    "the LLM should have left it null — accepting verdict anyway",
                    i,
                    test_id,
                    cdp,
                )
    return True, "", len(verdicts)


def _advance_to_triager(spec_dir: Path, project_dir: Path) -> None:
    """Schedule the Triager after evaluator's success path.

    Lazy import — same defensive shape as gen_functional's
    _advance_to_evaluator. Gated by ``PFACTORY_AUTO_TRIAGE`` (default
    ON; tests pin off).
    """
    try:
        from agents.triager import schedule_triager

        schedule_triager(spec_dir, project_dir, mode="initial")
    except ImportError as exc:
        _eval_log.warning(
            "could not auto-schedule triager: %s",
            exc,
        )


# ─── The agent itself ───────────────────────────────────────────────────


async def run_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the PFactory Evaluator agent.

    Args:
        spec_dir: PFactory workspace spec directory.
        project_dir: AIFactory project root (passed to docker runner +
            available to the LLM via Read/Glob/Grep).
        mode: 'initial' on first run; 'rerun' if invoked after a
            Triager-requested re-evaluation. Reserved — both modes
            currently share behaviour but the value is surfaced in
            status.json + verdicts.json for traceability.
        verbose: forwarded to ``run_agent_session``.

    Returns:
        True on a clean evaluation pass (including empty-test case);
        False on hard failure.

    Status transitions:
      generated   → evaluating          (in-flight marker)
                  → evaluated            (verdicts.json validated)
                  → evaluated_empty     (no tests to evaluate)
                  → evaluator_failed    (validation / session error)
    """
    try:
        _write_status_patch(
            spec_dir,
            status="evaluating",
            phase=f"evaluator_{mode}_started",
        )

        # 1. Load the plan + filter to completed functional subtasks.
        plan_path = spec_dir / "test_plan.json"
        if not plan_path.exists():
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_no_plan",
                evaluator_error="test_plan.json not found",
            )
            return False

        try:
            plan = json.loads(plan_path.read_text())
        except json.JSONDecodeError as exc:
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_plan_unparseable",
                evaluator_error=f"test_plan.json invalid: {exc}",
            )
            return False

        unit_completed = _completed_functional_subtasks(plan)
        browser_completed = _completed_browser_subtasks(plan)
        api_completed = _completed_api_subtasks(plan)
        jest_completed = _completed_jest_subtasks(plan)
        completed = unit_completed + browser_completed + api_completed + jest_completed

        # 2. No work — early exit with evaluated_empty.
        if not completed:
            verdicts_dir = spec_dir / "findings"
            verdicts_dir.mkdir(parents=True, exist_ok=True)
            (verdicts_dir / "verdicts.json").write_text(
                json.dumps(
                    {
                        "evaluator_version": "task7-commit5",
                        "mode": mode,
                        "verdicts": [],
                        "generated_at": _now_iso(),
                    },
                    indent=2,
                )
            )
            _write_status_patch(
                spec_dir,
                status="evaluated_empty",
                phase="evaluator_no_completed_subtasks",
                verdicts_count=0,
            )
            return True

        # 3. Per-test signal computation (real primitives; runner_fn
        #    seam mocked in tests so docker isn't required).
        bundles = []
        if unit_completed:
            unit_runner = _resolve_runner_fn(spec_dir, project_dir)
            bundles += [
                _build_signal_bundle(spec_dir, project_dir, st, unit_runner)
                for st in unit_completed
            ]
        if browser_completed:
            for st in browser_completed:
                # Stage the generated spec into the checkout so the Playwright
                # runner (mounts project_dir at /repo) can see it.
                _stage_browser_test(spec_dir, project_dir, st)
                # A kubernetes target has no static base_url — port-forward it
                # for the run lifetime (#108); else use the static target URL.
                rt = _kube_runtime_for(_resolve_target(spec_dir, st))
                if rt is not None:
                    with rt as runtime:
                        browser_runner = _resolve_browser_runner_fn(
                            runtime.target_url, spec_dir=spec_dir, subtask=st
                        )
                        bundles.append(
                            _build_browser_signal_bundle(spec_dir, project_dir, st, browser_runner)
                        )
                else:
                    url = _browser_target_url(spec_dir, st)
                    browser_runner = _resolve_browser_runner_fn(url, spec_dir=spec_dir, subtask=st)
                    bundles.append(
                        _build_browser_signal_bundle(spec_dir, project_dir, st, browser_runner)
                    )
        if api_completed:
            for st in api_completed:
                # network="host" so the in-container httpx test can reach the
                # host-served app at the target URL (e.g. http://localhost:8200).
                # A kubernetes target is port-forwarded for the run lifetime (#108).
                rt = _kube_runtime_for(_resolve_target(spec_dir, st))
                if rt is not None:
                    with rt as runtime:
                        api_runner = _resolve_runner_fn(
                            spec_dir,
                            project_dir,
                            network="host",
                            target_url=runtime.target_url,
                            subtask=st,
                        )
                        bundles.append(
                            _build_api_signal_bundle(spec_dir, project_dir, st, api_runner)
                        )
                else:
                    url = _browser_target_url(spec_dir, st)
                    api_runner = _resolve_runner_fn(
                        spec_dir,
                        project_dir,
                        network="host",
                        target_url=url,
                        subtask=st,
                    )
                    bundles.append(_build_api_signal_bundle(spec_dir, project_dir, st, api_runner))
        if jest_completed:
            jest_runner = _resolve_jest_runner_fn()
            bundles += [
                _build_jest_signal_bundle(spec_dir, project_dir, st, jest_runner)
                for st in jest_completed
            ]

        # 4. Build prompt + invoke SDK session.
        from prompts_pkg.prompts import get_pfactory_evaluator_prompt

        prompt = get_pfactory_evaluator_prompt(spec_dir, project_dir, bundles)
        client = await _resolve_evaluator_client(spec_dir, project_dir)
        try:
            session_status, _response, _err = await _invoke_session(
                client,
                prompt,
                spec_dir,
                verbose,
            )
        except Exception as exc:  # noqa: BLE001 — surface in status
            _eval_log.error("evaluator session raised: %s\n%s", exc, traceback.format_exc())
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_session_error",
                evaluator_error=str(exc)[:500],
            )
            return False

        # 5. Validate the verdicts.json the agent wrote.
        verdicts_path = spec_dir / "findings" / "verdicts.json"
        ok, err, count = _validate_verdicts(verdicts_path)
        if not ok:
            _write_status_patch(
                spec_dir,
                status="evaluator_failed",
                phase="evaluator_invalid_verdicts",
                evaluator_error=err,
            )
            return False

        _write_status_patch(
            spec_dir,
            status="evaluated",
            phase="evaluator_complete",
            verdicts_count=count,
            tests_evaluated=len(bundles),
        )
        # Forward-chain to the Triager (Task 8, #9). Gated by
        # ``PFACTORY_AUTO_TRIAGE`` env; tests pin it off to keep
        # this layer deterministic.
        _advance_to_triager(spec_dir, project_dir)
        return True

    except Exception as exc:
        _eval_log.error("evaluator failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="evaluator_failed",
            phase=f"evaluator_{mode}_exception",
            evaluator_error=str(exc)[:500],
        )
        return False


# ─── Auto-fire scheduler ─────────────────────────────────────────────────
#
# Same GC-anchor pattern as _BG_PLANNER_TASKS and _BG_GEN_FUNCTIONAL_TASKS.
# Gen-Functional's success path (status=generated, tests_generated >= 1)
# calls schedule_evaluator after writing the status — gated on env so the
# test suite stays deterministic.

_BG_EVALUATOR_TASKS: set[asyncio.Task] = set()


def schedule_evaluator(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "rerun"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget Evaluator, gated by ``PFACTORY_AUTO_EVALUATE``.

    Default ON (env var unset or "1"). Test fixtures should set
    ``PFACTORY_AUTO_EVALUATE=0`` to keep gen_functional's success path
    from auto-advancing.

    Returns the scheduled asyncio.Task, or None if the env var disables
    auto-evaluation. Each scheduled task is anchored in
    ``_BG_EVALUATOR_TASKS`` until done (cleared via done_callback).
    """
    if os.environ.get("PFACTORY_AUTO_EVALUATE", "1") == "0":
        return None
    task = asyncio.create_task(run_evaluator(spec_dir, project_dir, mode=mode))
    _BG_EVALUATOR_TASKS.add(task)
    task.add_done_callback(_BG_EVALUATOR_TASKS.discard)
    return task
