"""Browser lane (Playwright) — selection, runner_fn, AppRuntime + signal bundle.

Extracted verbatim from ``agents/evaluator.py`` (issue #193 god-file split).
The browser lane has two execution paths:

  * static base_url — run the generated Playwright spec in the runner image
    against a remote URL (e.g. a deployed Pages site).
  * docker-compose AppRuntime — ``_run_browser_subtask_with_runtime`` wraps the
    AppRuntime + DockerRunner lifecycle and owns the Browser-lane status.json
    phase transitions (``executor_app_running`` / ``app_not_healthy``).

Coverage is skipped (Decision 11) and mutation is skipped (no TS mutation in the
browser path).

Behaviour is identical to the original module — this is a pure move.
"""

from __future__ import annotations

import shutil as _sh
import tempfile as _tmp
from pathlib import Path

from agents.lanes.common import (
    _PLAYWRIGHT_IMAGE,
    _eval_log,
    _test_credential_specs,
    _write_status_patch,
)
from agents.lanes.signals import (
    EvaluatorSignals,
    _flaky_history_for_subtask,
    _lint_promotion_for_subtask,
    _stability_for_subtask,
)
from tools.runners.app_runtime import AppRuntime, AppRuntimeError
from tools.runners.docker_runner import DockerRunner, DockerRunResult
from tools.runners.sandbox_credentials import resolve_test_target_credentials


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


def _stage_browser_test(spec_dir: Path, project_dir: Path, subtask: dict) -> None:
    """Copy the generated test from the workspace into the project checkout so
    the playwright runner (which mounts project_dir at /repo) can see it."""
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

    def _run(test_file: Path, project_dir_arg: Path, _seed: int) -> DockerRunResult:
        # relative path of the spec inside the project checkout
        try:
            rel = str(Path(test_file).resolve().relative_to(Path(project_dir_arg).resolve()))
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
    """Signal bundle for a Browser-lane subtask: 3x stability via Playwright,
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
