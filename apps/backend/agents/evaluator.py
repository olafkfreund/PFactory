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
"""

from __future__ import annotations

import asyncio
import json
import logging as _logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

_eval_log = _logging.getLogger(__name__)


# ─── Workspace helpers (local copy — same pattern as planner/gen_functional)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_status(spec_dir: Path) -> dict:
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Best-effort push-based progress event (#95); no-op unless opted in.
    from agents.stage_events import emit_stage_event

    emit_stage_event(spec_dir, status, stage="evaluator")


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


# ─── Runner-fn seam for stability + mutation primitives ─────────────────


class _RunResultLike(Protocol):
    """Same duck-type as stability_runner/mutate_probe expect."""

    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


_PYTEST_IMAGE = "pfactory-runner-pytest:latest"


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

            sandbox_creds = resolve_sandbox_credentials(
                project_dir_arg, spec_dir, network
            )
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


# ─── Per-test signal bundle ─────────────────────────────────────────────


@dataclass
class EvaluatorSignals:
    """Per-test bundle of the four pre-computed signals plus identity.

    The fifth signal (semantic relevance) is the LLM's call — it
    doesn't live in this dataclass.

    Any of the four signal fields can be ``None`` if the primitive
    couldn't run (e.g., coverage XML not emitted by the Executor for
    this test). The prompt helper renders missing signals as
    "not computed" rather than crashing.

    ``coverage_delta`` is explicitly ``None`` (not zero) when the
    framework's ``coverage_strategy == "skip"`` (Decision 11 — Browser
    lane).  This prevents the Evaluator prompt from seeing "0% coverage"
    and issuing a spurious reject for Playwright tests.  A ``None``
    value is rendered as "N/A (browser lane)" by
    ``_format_evaluator_per_test_block``.
    """

    test_id: str
    test_file: Path
    target: str
    rationale: str
    coverage_delta: Any = None  # CoverageDelta | None  (None = skip-coverage lane)
    stability: Any = None  # StabilityResult | None
    mutation: Any = None  # MutationResult | None
    lint_promotion: Any = None  # PromotionResult | None
    flaky_history: Any = None  # FlakyHistory | None  (cross-run flip-rate, #37)


# ─── Signal-bundle assembly ─────────────────────────────────────────────


def _completed_functional_subtasks(plan: dict) -> list[dict]:
    """Pick subtasks that Gen-Functional successfully generated
    (status='completed', lane in {'unit','functional'}, has files_to_create).

    Accepts both the v0.2 'unit' lane and the v0.1 deprecated 'functional'
    alias so old test_plan.json files still process. v0.3 removes the
    'functional' alias.
    """
    out = []
    for phase in plan.get("phases", []):
        for st in phase.get("subtasks", []):
            # The pytest runner only handles Python. A unit-lane subtask in
            # another language (e.g. Jest/TypeScript) needs its own runner
            # image — skip it here rather than feeding a .test.ts to pytest.
            if (
                st.get("status") == "completed"
                and st.get("lane") in ("unit", "functional")
                and (st.get("language") in (None, "python"))
                and st.get("files_to_create")
            ):
                out.append(st)
    return out


def _framework_coverage_strategy(subtask: dict) -> str | None:
    """Look up the framework descriptor's coverage_strategy for a subtask.

    Returns the strategy string ("lcov", "cobertura", "skip") or None
    if the subtask has no ``framework`` field or the registry lookup
    fails (e.g. unknown framework name — v0.1 back-compat).

    Failures are swallowed and logged at DEBUG level so a registry
    misconfiguration never blocks the Evaluator.
    """
    framework_name = subtask.get("framework")
    if not framework_name:
        return None
    try:
        from framework_registry import load_registry

        registry = load_registry()
        desc = registry.get(framework_name)
        if desc is None:
            _eval_log.debug(
                "coverage_strategy: framework %r not in registry; treating as numeric",
                framework_name,
            )
            return None
        return desc.coverage_strategy
    except Exception as exc:  # noqa: BLE001 — never block the Evaluator
        _eval_log.debug(
            "coverage_strategy lookup failed for framework %r: %s",
            framework_name,
            exc,
        )
        return None


# ─── Browser-lane AppRuntime status transitions (Task 8 / #24) ──────────


def _run_browser_subtask_with_runtime(
    spec_dir: Path,
    subtask: dict,
    runner_fn=None,
    *,
    target=None,
    repo_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Execute a Browser-lane subtask wrapped in an AppRuntime lifecycle.

    Writes status.json phase transitions visible to the portal:

      ``executor_app_running`` — docker-compose is up + healthy; Playwright
                                  container is executing.
      ``app_not_healthy``      — health-poll timed out; the error message
                                  includes the last observed status code per
                                  URL.

    This function is intentionally side-effect-light — it owns ONLY the
    ``status.json`` writes.  The actual docker-compose / HTTP-poll / container
    execution lives in ``tools.runners.app_runtime`` and
    ``tools.runners.lane_dispatch``.

    Args:
        spec_dir: PFactory workspace spec directory.
        subtask: A subtask dict from test_plan.json (lane == "browser" or
            "integration").
        runner_fn: Injectable subprocess.run replacement for tests.
        target: A ``DockerComposeTarget`` instance.  When ``None`` this
            function is a no-op and returns ``(False, "no_target")``.
        repo_root: Absolute path to the AIFactory project root (required
            when ``target`` is not None).

    Returns:
        ``(success, error_phase)`` — where ``success=True`` means the
        Playwright container ran (its exit code is separate), and
        ``error_phase`` is set when AppRuntime itself failed (e.g.
        ``"app_not_healthy"``).
    """
    if target is None:
        # No DockerComposeTarget — Browser subtask with a static base_url;
        # skip AppRuntime lifecycle entirely.
        return False, "no_target"

    from tools.runners.app_runtime import AppRuntime, AppRuntimeError

    _write_status_patch(
        spec_dir,
        phase="executor_app_running",
        browser_subtask_id=subtask.get("id", ""),
    )

    runtime_kwargs: dict = {}
    if runner_fn is not None:
        runtime_kwargs["runner_fn"] = runner_fn

    try:
        with AppRuntime(target, repo_root, **runtime_kwargs) as runtime:
            try:
                runtime.wait_for_healthy()
            except AppRuntimeError as exc:
                _eval_log.error(
                    "app_not_healthy for subtask %s: %s",
                    subtask.get("id", ""),
                    exc,
                )
                _write_status_patch(
                    spec_dir,
                    phase="app_not_healthy",
                    app_runtime_error=str(exc)[:500],
                    browser_subtask_id=subtask.get("id", ""),
                )
                return False, "app_not_healthy"
            # App is healthy — caller proceeds with the test run.
            return True, None
    except AppRuntimeError as exc:
        # start() itself failed (compose up returned non-zero).
        _eval_log.error(
            "app_runtime start failed for subtask %s: %s",
            subtask.get("id", ""),
            exc,
        )
        _write_status_patch(
            spec_dir,
            phase="app_not_healthy",
            app_runtime_error=str(exc)[:500],
            browser_subtask_id=subtask.get("id", ""),
        )
        return False, "app_not_healthy"


def _coverage_delta_for_subtask(
    spec_dir: Path,
    subtask: dict,
):
    """Try to compute coverage delta for one test.

    Returns ``None`` in two distinct cases:

    1. **Skip-coverage lane** (Decision 11): the subtask's framework has
       ``coverage_strategy == "skip"`` (e.g. Playwright Browser lane).
       The Evaluator prompt renders this as "N/A (browser lane)" and does
       NOT penalise the test for zero coverage.

    2. **Coverage XML absent**: baseline or per-test coverage.xml are
       missing — the LLM will see "not computed" (pre-existing behaviour).

    Looks for ``spec_dir/findings/baseline_coverage.xml`` and
    ``spec_dir/findings/runs/<test_id>/coverage.xml`` for case (2).
    """
    # Case 1: framework explicitly opted out of coverage measurement.
    strategy = _framework_coverage_strategy(subtask)
    if strategy == "skip":
        _eval_log.debug(
            "coverage_delta: framework %r uses skip strategy — returning None",
            subtask.get("framework"),
        )
        return None

    # Case 2: try to parse XML coverage artefacts.
    from agents.coverage_delta import compute_delta_from_paths

    baseline = spec_dir / "findings" / "baseline_coverage.xml"
    after = spec_dir / "findings" / "runs" / subtask["id"] / "coverage.xml"
    if not baseline.exists() or not after.exists():
        return None
    try:
        return compute_delta_from_paths(baseline, after)
    except Exception as exc:  # noqa: BLE001 — defensive
        _eval_log.warning(
            "coverage_delta failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _stability_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the 3× stability check for one test."""
    from agents.stability_runner import check_stability

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        return check_stability(test_file, project_dir, runner_fn)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "stability check failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _flaky_history_for_subtask(spec_dir: Path, subtask: dict, stability):
    """Record this run's pass/fail outcome into the project-level flaky
    history store and return the updated FlakyHistory (#37).

    The outcome is derived from the 3× stability verdict: ``STABLE`` is a
    clean pass; anything else (flaky / consistent-fail / error) is a fail.
    Returns ``None`` when stability couldn't run, so we don't pollute the
    history with a phantom outcome. The store lives one level above the
    spec dir (``<workspace>/<project>/test_history.json``) so it persists
    across separate spec runs of the same project.
    """
    if stability is None:
        return None
    from agents.flaky_history import record_outcome
    from agents.stability_runner import StabilityVerdict

    try:
        store = spec_dir.parent.parent / "test_history.json"
        passed = stability.verdict == StabilityVerdict.STABLE
        return record_outcome(store, subtask["id"], passed)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "flaky-history record failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _mutation_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the mutate-and-check probe for one test, dispatched by language.

    Routes to the Python (mutmut-style AST) or TypeScript (Stryker) backend
    via ``mutation_dispatch`` (#41). Writes the mutant to
    ``spec_dir/findings/mutants/<test_id>.<ext>`` so the original test file
    stays clean. Returns ``None`` for languages with no wired backend.
    """
    from agents.mutation_dispatch import (
        is_mutation_supported,
        mutant_extension,
        run_language_mutation,
    )

    language = subtask.get("language")
    if not is_mutation_supported(language):
        return None
    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    ext = mutant_extension(language)
    mutant_path = spec_dir / "findings" / "mutants" / f"{subtask['id']}.{ext}"
    try:
        return run_language_mutation(
            language, test_file, project_dir, runner_fn, mutant_path=mutant_path
        )
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "mutate probe failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _lint_promotion_for_subtask(spec_dir: Path, subtask: dict):
    """Run flake_risk_lint + promote findings for one test."""
    from agents.flake_risk_lint import flake_risk_lint
    from agents.lint_promotion import promote_flake_findings

    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        source = test_file.read_text()
    except OSError:
        return None
    result = flake_risk_lint(source)
    return promote_flake_findings(result, source)


def _build_signal_bundle(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
) -> EvaluatorSignals:
    """Run every available signal primitive against ``subtask`` and
    return a bundle the prompt helper can format."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=_coverage_delta_for_subtask(spec_dir, subtask),
        stability=stability,
        mutation=_mutation_for_subtask(spec_dir, project_dir, subtask, runner_fn),
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


# ─── Browser-lane signal computation (static base_url path) ─────────────
#
# Wires the browser lane into run_evaluator for the common static-URL case
# (an http target in .pfactory.yml — e.g. a deployed Pages site). The
# docker-compose AppRuntime path (_run_browser_subtask_with_runtime) remains
# for local-app targets; this path runs the generated Playwright test in the
# playwright runner image against the remote URL with --network=bridge and
# PFACTORY_TARGET_URL injected.

_PLAYWRIGHT_IMAGE = "pfactory-runner-playwright:latest"


def _completed_browser_subtasks(plan: dict) -> list[dict]:
    """Completed Playwright/browser subtasks Gen-Functional generated."""
    out = []
    for phase in plan.get("phases", []):
        for st in phase.get("subtasks", []):
            if (
                st.get("status") == "completed"
                and st.get("lane") == "browser"
                and st.get("files_to_create")
            ):
                out.append(st)
    return out


def _browser_target_url(spec_dir: Path, subtask: dict) -> str | None:
    """Resolve the base_url for the subtask's target from the snapshotted
    .pfactory.yml (context/pfactory_yml.json). Falls back to the default
    target when the subtask has no target_name.

    The trailing slash is stripped: the parser normalises base_url to end in
    ``/``, but api tests build URLs as ``f"{base_url}/api/..."`` — a trailing
    slash would produce ``//api/...`` (a different path → spurious 404s). A
    bare origin is also valid for Playwright ``page.goto``.
    """
    ctx = spec_dir / "context" / "pfactory_yml.json"
    if not ctx.exists():
        return None
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    targets = cfg.get("targets") or []
    want = subtask.get("target_name") or cfg.get("default_target")

    def _norm(u: str) -> str:
        # Strip a single trailing slash from the origin/path, but never reduce
        # to empty (keep at least the scheme+host).
        return u[:-1] if (u.endswith("/") and not u.endswith("://")) else u

    for t in targets:
        if t.get("name") == want and t.get("base_url"):
            return _norm(t["base_url"])
    # last resort: first http target with a base_url
    for t in targets:
        if t.get("base_url"):
            return _norm(t["base_url"])
    return None


def _resolve_target(spec_dir: Path, subtask: dict) -> dict | None:
    """Resolve the subtask's *target object* from the snapshotted .pfactory.yml.

    Like :func:`_browser_target_url` but returns the whole target dict (so the
    caller can branch on ``type``), preferring the subtask's ``target_name``,
    then the config ``default_target``, then the first target.
    """
    ctx = spec_dir / "context" / "pfactory_yml.json"
    if not ctx.exists():
        return None
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    targets = cfg.get("targets") or []
    want = subtask.get("target_name") or cfg.get("default_target")
    for t in targets:
        if t.get("name") == want:
            return t
    return targets[0] if targets else None


def _kube_runtime_for(target: dict | None, *, runtime_cls=None):
    """A ``KubernetesRuntime`` for a kubernetes port-forward target, else None (#108).

    When the resolved target is ``type: kubernetes`` with ``port_forward: true``,
    the api/browser lane has no static ``base_url`` — the URL only exists while a
    ``kubectl port-forward`` is live. The caller uses the returned runtime as a
    context manager so the forward is up during the test run and torn down after
    (on success *and* failure). Auth rides the materialised read-only kubeconfig
    (``KUBECONFIG``). Returns None for non-k8s targets (the static-URL path).
    """
    if (
        not target
        or target.get("type") != "kubernetes"
        or not target.get("port_forward")
    ):
        return None
    from types import SimpleNamespace

    from tools.runners.kubernetes_runtime import KubernetesRuntime

    cls = runtime_cls or KubernetesRuntime
    t = SimpleNamespace(
        name=target.get("name"),
        context=target.get("context"),
        namespace=target.get("namespace"),
        service=target.get("service"),
        port=target.get("port"),
        # KubernetesRuntime.start() reads target.port_forward — without it the
        # real runtime AttributeErrors (the mocked dispatch test never hits
        # start(), so this only surfaces live). #108.
        port_forward=target.get("port_forward"),
    )
    return cls(t, kubeconfig=os.environ.get("KUBECONFIG"))


def _test_credential_specs(spec_dir: Path, subtask: dict | None) -> list:
    """TargetCredentialSpec list for a subtask's ``ref``-auth target (#107).

    Reads the snapshotted .pfactory.yml: when the subtask's target uses
    ``auth: {type: ref}``, turn the referenced ``test_credentials`` entry into a
    resolver spec (``resolve_test_target_credentials`` injects it as login env).
    Empty list when there is no ref-auth / no matching credential.
    """
    if subtask is None:
        return []
    from tools.runners.sandbox_credentials import TargetCredentialSpec

    target = _resolve_target(spec_dir, subtask)
    auth = (target or {}).get("auth") or {}
    if auth.get("type") != "ref" or not auth.get("ref"):
        return []
    ctx = spec_dir / "context" / "pfactory_yml.json"
    try:
        cfg = json.loads(ctx.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    entry = (cfg.get("test_credentials") or {}).get(auth["ref"])
    if not entry:
        return []
    return [
        TargetCredentialSpec(
            name=auth["ref"],
            ref=entry["ref"],
            as_secret=entry["as_secret"],
            as_username=entry.get("as_username"),
            username_ref=entry.get("username_ref"),
        )
    ]


def _stage_browser_test(spec_dir: Path, project_dir: Path, subtask: dict) -> None:
    """Copy the generated test from the workspace into the project checkout so
    the playwright runner (which mounts project_dir at /repo) can see it."""
    import shutil as _sh

    rel = subtask["files_to_create"][0]
    src = spec_dir / rel
    dst = Path(project_dir) / rel
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        _sh.copy2(src, dst)


def _resolve_browser_runner_fn(
    target_url: str | None,
    image: str = _PLAYWRIGHT_IMAGE,
    *,
    spec_dir: Path | None = None,
    subtask: dict | None = None,
):
    """Return a runner_fn(test_file, project_dir, seed) -> DockerRunResult that
    runs ONE Playwright spec in the runner image against ``target_url``.

    Mirrors the proven invocation: world-writable scratch, copy /repo→/scratch,
    symlink node_modules to the image's global install, --network=bridge, and
    PFACTORY_TARGET_URL injected so the spec hits the deployed site. When the
    target uses ``auth: {type: ref}`` (#107), the login credential is resolved
    and injected as env so the spec's login fixture can authenticate.
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        # relative path of the spec inside the project checkout
        try:
            rel = str(
                Path(test_file).resolve().relative_to(Path(project_dir_arg).resolve())
            )
        except ValueError:
            # test_file lives in the workspace, not the checkout — use its
            # path relative to the workspace tests/ layout instead.
            rel = "/".join(Path(test_file).parts[-3:])
        scratch = Path(_tmp.mkdtemp(prefix="tf-pw-"))
        try:
            scratch.chmod(0o777)
            runner = DockerRunner(image=image, network="host", read_only_rootfs=False)
            # DockerRunner mounts the checkout read-only at /work and a
            # writable scratch at /scratch (the workdir). Stage the project
            # into scratch so node_modules (symlinked to the image's global
            # install) resolves and Playwright can write artifacts.
            staged = (
                "cp -r /work/. /scratch/ 2>/dev/null; "
                "ln -sfn /usr/lib/node_modules /scratch/node_modules; "
                "cd /scratch && "
                f"npx playwright test {rel} --reporter=junit "
                "--output=/scratch/pw-artifacts; "
                "echo __PW_EXIT=$?"
            )
            extra_env = {}
            if target_url:
                extra_env["PFACTORY_TARGET_URL"] = target_url
                extra_env["APP_URL"] = target_url
            # Test-target login credentials (#107): inject the ref-auth
            # target's username/secret so the spec's login fixture can sign in.
            # network="host" here, so the egress-gated resolver runs.
            from tools.runners.sandbox_credentials import (
                resolve_test_target_credentials,
            )

            test_creds = resolve_test_target_credentials(
                _test_credential_specs(spec_dir, subtask) if spec_dir else [],
                project_dir_arg,
                spec_dir,
                "host",
            )
            extra_env.update(test_creds.env)
            try:
                res = runner.run(
                    repo_path=Path(project_dir_arg).resolve(),
                    scratch_path=scratch.resolve(),
                    command=["sh", "-c", staged],
                    extra_env=extra_env,
                    timeout_sec=300,
                )
            finally:
                test_creds.wipe()
            # The wrapper shell always exits 0; recover the real playwright exit
            # from the __PW_EXIT marker so stability sees the true pass/fail.
            code = res.returncode
            marker = None
            for line in (res.stdout or "").splitlines():
                if line.startswith("__PW_EXIT="):
                    try:
                        marker = int(line.split("=", 1)[1])
                    except ValueError:
                        marker = None
            if marker is not None:
                code = marker
            return DockerRunResult(
                returncode=code, stdout=res.stdout, stderr=res.stderr, argv=res.argv
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


def _build_browser_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for a Browser-lane subtask: 3× stability via Playwright,
    coverage skipped (Decision 11), mutation skipped (no TS mutation in the
    browser path)."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,  # browser lane — coverage_strategy == "skip"
        stability=stability,
        mutation=None,  # mutation not run for the browser lane
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


# ─── API-lane signal computation (httpx against a host-served app) ──────


def _completed_api_subtasks(plan: dict) -> list[dict]:
    """Completed api-lane subtasks (httpx tests) Gen-Functional generated."""
    out = []
    for phase in plan.get("phases", []):
        for st in phase.get("subtasks", []):
            if (
                st.get("status") == "completed"
                and st.get("lane") == "api"
                and st.get("files_to_create")
            ):
                out.append(st)
    return out


def _build_api_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for an api-lane subtask: 3× stability via pytest/httpx
    against the host-served app, coverage skipped (the SUT runs out-of-process),
    mutation skipped."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,  # api lane hits a running service — no line coverage
        stability=stability,
        mutation=None,
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


# ─── Jest-lane signal computation (TypeScript unit tests) ───────────────

_JEST_IMAGE = "pfactory-runner-jest:latest"


def _completed_jest_subtasks(plan: dict) -> list[dict]:
    """Completed unit-lane TypeScript (Jest) subtasks Gen-Functional generated.

    These sit in the unit lane like pytest, but are TypeScript — so they need
    the Jest runner, not pytest.
    """
    out = []
    for phase in plan.get("phases", []):
        for st in phase.get("subtasks", []):
            if (
                st.get("status") == "completed"
                and st.get("lane") in ("unit", "functional")
                and st.get("language") == "typescript"
                and st.get("files_to_create")
            ):
                out.append(st)
    return out


def _resolve_jest_runner_fn(image: str = _JEST_IMAGE):
    """Return a runner_fn(test_file, project_dir, seed) -> DockerRunResult that
    runs ONE Jest/TypeScript spec in the runner image.

    The SUT (.ts modules + jest.config + tsconfig) and the test are flattened
    into a writable scratch dir so the test's relative ``./module`` import
    resolves; node_modules is symlinked to the image's global install and
    NODE_PATH spans jest's nested deps (ts-jest requires jest-util).
    """
    import shutil as _sh
    import tempfile as _tmp

    from tools.runners.docker_runner import DockerRunner, DockerRunResult

    def _run(test_file: Path, project_dir_arg: Path, seed: int) -> DockerRunResult:
        scratch = Path(_tmp.mkdtemp(prefix="tf-jest-"))
        try:
            # Copy the SUT (.ts modules + jest/ts config) into scratch root, then
            # place the test at its ORIGINAL relative path (e.g. tests/x.test.ts)
            # so its relative import (`../slugify` from a tests/ subdir, or
            # `./slugify` from the root) resolves the same way it was authored.
            for item in Path(project_dir_arg).iterdir():
                if item.name in (".git", "node_modules"):
                    continue
                dst = scratch / item.name
                if item.is_dir():
                    _sh.copytree(item, dst, dirs_exist_ok=True)
                else:
                    _sh.copy2(item, dst)
            tparts = Path(test_file).parts
            if "tests" in tparts:
                rel = Path(*tparts[tparts.index("tests") :])  # tests/<...>/x.test.ts
            else:
                rel = Path(Path(test_file).name)
            dst_test = scratch / rel
            dst_test.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(test_file, dst_test)
            for p in scratch.rglob("*"):
                try:
                    p.chmod(0o777)
                except OSError:
                    pass
            scratch.chmod(0o777)

            runner = DockerRunner(image=image, network="none", read_only_rootfs=False)
            node_path = (
                "/usr/local/lib/node_modules:"
                "/usr/local/lib/node_modules/jest/node_modules"
            )
            cmd = (
                "ln -sfn /usr/local/lib/node_modules /scratch/node_modules; "
                "cd /scratch && "
                f"npx jest --ci --forceExit {rel.as_posix()} 2>&1; "
                "echo __JEST_EXIT=$?"
            )
            res = runner.run(
                repo_path=Path(project_dir_arg).resolve(),
                scratch_path=scratch.resolve(),
                command=["sh", "-c", cmd],
                extra_env={"NODE_PATH": node_path},
                timeout_sec=300,
            )
            code = res.returncode
            for line in (res.stdout or "").splitlines():
                if line.startswith("__JEST_EXIT="):
                    try:
                        code = int(line.split("=", 1)[1])
                    except ValueError:
                        pass
            return DockerRunResult(
                returncode=code, stdout=res.stdout, stderr=res.stderr, argv=res.argv
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


def _build_jest_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for a Jest (TypeScript unit) subtask: 3× stability via
    Jest; coverage + mutation skipped in the demo path."""
    stability = _stability_for_subtask(spec_dir, project_dir, subtask, runner_fn)
    return EvaluatorSignals(
        test_id=subtask["id"],
        test_file=spec_dir / subtask["files_to_create"][0],
        target=subtask.get("target") or "?",
        rationale=subtask.get("rationale") or "?",
        coverage_delta=None,
        stability=stability,
        mutation=None,
        lint_promotion=_lint_promotion_for_subtask(spec_dir, subtask),
        flaky_history=_flaky_history_for_subtask(spec_dir, subtask, stability),
    )


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
                            _build_browser_signal_bundle(
                                spec_dir, project_dir, st, browser_runner
                            )
                        )
                else:
                    url = _browser_target_url(spec_dir, st)
                    browser_runner = _resolve_browser_runner_fn(
                        url, spec_dir=spec_dir, subtask=st
                    )
                    bundles.append(
                        _build_browser_signal_bundle(
                            spec_dir, project_dir, st, browser_runner
                        )
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
                            _build_api_signal_bundle(
                                spec_dir, project_dir, st, api_runner
                            )
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
                    bundles.append(
                        _build_api_signal_bundle(spec_dir, project_dir, st, api_runner)
                    )
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
            _eval_log.error(
                "evaluator session raised: %s\n%s", exc, traceback.format_exc()
            )
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
