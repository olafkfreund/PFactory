"""Jest lane (TypeScript unit tests) — selection, runner_fn + signal bundle.

Extracted verbatim from ``agents/evaluator.py`` (issue #193 god-file split).
Jest subtasks sit in the unit lane but are TypeScript, so they need the Jest
runner image rather than pytest. 3x stability via Jest; coverage + mutation
are skipped in the demo path.

Behaviour is identical to the original module — this is a pure move.
"""

from __future__ import annotations

import contextlib
import shutil as _sh
import tempfile as _tmp
from pathlib import Path

from agents.lanes.common import _JEST_IMAGE
from agents.lanes.signals import (
    EvaluatorSignals,
    _flaky_history_for_subtask,
    _lint_promotion_for_subtask,
    _stability_for_subtask,
)
from tools.runners.docker_runner import DockerRunner, DockerRunResult


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

    def _run(test_file: Path, project_dir_arg: Path, _seed: int) -> DockerRunResult:
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
                with contextlib.suppress(OSError):
                    p.chmod(0o777)
            scratch.chmod(0o777)

            runner = DockerRunner(image=image, network="none", read_only_rootfs=False)
            node_path = "/usr/local/lib/node_modules:/usr/local/lib/node_modules/jest/node_modules"
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
                    with contextlib.suppress(ValueError):
                        code = int(line.split("=", 1)[1])
            return DockerRunResult(
                returncode=code, stdout=res.stdout, stderr=res.stderr, argv=res.argv
            )
        finally:
            _sh.rmtree(scratch, ignore_errors=True)

    return _run


def _build_jest_signal_bundle(
    spec_dir: Path, project_dir: Path, subtask: dict, runner_fn
) -> EvaluatorSignals:
    """Signal bundle for a Jest (TypeScript unit) subtask: 3x stability via
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
