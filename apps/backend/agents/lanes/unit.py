"""Unit lane (pytest, Python) — subtask selection + full signal bundle.

Extracted verbatim from ``agents/evaluator.py`` (issue #193 god-file split).
This is the only lane that runs the full signal set: coverage delta, 3x stability,
mutation, lint promotion, and flaky history. The other lanes (api/browser/jest)
deliberately skip coverage + mutation.

Behaviour is identical to the original module — this is a pure move.
"""

from __future__ import annotations

from pathlib import Path

from agents.lanes.signals import (
    EvaluatorSignals,
    _coverage_delta_for_subtask,
    _flaky_history_for_subtask,
    _lint_promotion_for_subtask,
    _mutation_for_subtask,
    _stability_for_subtask,
)


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
