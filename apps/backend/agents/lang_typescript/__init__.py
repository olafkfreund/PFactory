"""TypeScript / JavaScript language primitives for the PFactory Evaluator.

This package mirrors the Python primitives in the parent ``agents/`` package
(``preflight_static``, ``flake_risk_lint``, ``mutate_probe``) for the
TypeScript / JavaScript ecosystem.

The three modules expose the same public API shape as their Python siblings
so the Evaluator can dispatch by language without per-language branches:

  preflight    -> run_ts_preflight(test_file, project_dir, ...) -> TSPreflightReport
  flake_lint   -> run_ts_flake_lint(test_file, project_dir, ...) -> TSFlakeReport
  mutate_probe -> run_ts_mutate_probe(test_file, project_dir, ...) -> TSMutateReport

Each module wraps subprocess invocations of TS toolchain binaries that live
in Task 7's ``pfactory-runner-jest:latest`` / ``pfactory-runner-playwright:latest``
images.  Tests inject a ``runner_fn`` to stay hermetic.
"""

from __future__ import annotations

from agents.lang_typescript.flake_lint import (
    TSFlakeFinding,
    TSFlakeReport,
    run_ts_flake_lint,
)
from agents.lang_typescript.mutate_probe import (
    TSMutateReport,
    TSMutationVerdict,
    run_ts_mutate_probe,
)
from agents.lang_typescript.preflight import TSPreflightReport, run_ts_preflight

__all__ = [
    "TSPreflightReport",
    "run_ts_preflight",
    "TSFlakeFinding",
    "TSFlakeReport",
    "run_ts_flake_lint",
    "TSMutateReport",
    "TSMutationVerdict",
    "run_ts_mutate_probe",
]
