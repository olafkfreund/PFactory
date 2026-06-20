"""API lane (httpx against a host-served app) — selection + signal bundle.

Extracted verbatim from ``agents/evaluator.py`` (issue #193 god-file split).
The api lane runs 3x stability via pytest/httpx against the running service;
coverage and mutation are skipped (the SUT runs out-of-process).

Behaviour is identical to the original module — this is a pure move.
"""

from __future__ import annotations

from pathlib import Path

from agents.lanes.signals import (
    EvaluatorSignals,
    _flaky_history_for_subtask,
    _lint_promotion_for_subtask,
    _stability_for_subtask,
)


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
    """Signal bundle for an api-lane subtask: 3x stability via pytest/httpx
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
