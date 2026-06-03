"""Visual Inspection Run packager (#170 / P1 #171).

Assemble the committed run folder from a finished browser run's evidence:

    automated-test/<run-id>/
      report.md            meta.json
      screenshots/NN-<label>-<state>.png
      recording/video.webm  recording/trace.zip

Pure filesystem — the per-step screenshots + video + trace come from the
evidence the Playwright run already captured (``agents/evidence/``). P2 adds the
correction plan + GitHub export + downloads; P4 commits the folder to the SUT
repo. The screenshot naming convention is emitted by the visual-inspection test
template (``frameworks/playwright/library/visual-inspection.spec.ts.tmpl``).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .model import RunMeta, StepResult, slugify
from .report import render_inspection_report

__all__ = ["PackagedRun", "package_run"]


@dataclass(frozen=True)
class PackagedRun:
    """Result of packaging: the run dir + the artifact paths within it."""

    run_dir: Path
    report_md: Path
    meta_json: Path


def _step_filename(step: StepResult) -> str:
    """The conventional screenshot filename for a step (``03-submit-fail.png``)."""
    return f"{step.n:02d}-{slugify(step.label)}-{step.state}.png"


def package_run(
    out_root: Path | str,
    *,
    meta: RunMeta,
    evidence_dir: Path | str,
) -> PackagedRun:
    """Write ``<out_root>/<meta.id>/`` from the run's ``meta`` + evidence.

    Args:
        out_root: the ``automated-test/`` root (in the SUT repo or a staging dir).
        meta: the run summary (steps carry the ``screenshot`` filenames the test
            emitted, relative to ``evidence_dir``).
        evidence_dir: where the Playwright run wrote its artifacts (per-step
            screenshots named by the convention, plus the video / trace named in
            ``meta.video`` / ``meta.trace``).

    Returns:
        A :class:`PackagedRun` pointing at the written folder. Missing source
        artifacts are skipped (the report still renders); ``report.md`` +
        ``meta.json`` are always written.
    """
    evidence = Path(evidence_dir)
    run_dir = Path(out_root) / meta.id
    shots = run_dir / "screenshots"
    rec = run_dir / "recording"
    shots.mkdir(parents=True, exist_ok=True)
    rec.mkdir(parents=True, exist_ok=True)

    # Re-key each step's screenshot to the run-relative path the report/meta use.
    placed: list[StepResult] = []
    for step in meta.steps:
        rel = None
        if step.screenshot:
            src = evidence / step.screenshot
            dst_name = _step_filename(step)
            if src.is_file():
                shutil.copy2(src, shots / dst_name)
                rel = f"screenshots/{dst_name}"
        placed.append(
            StepResult(n=step.n, label=step.label, state=step.state,
                       screenshot=rel, error=step.error)
        )

    # Copy the recording, re-pointing meta to the run-relative paths.
    video_rel = _copy_if(evidence, meta.video, rec, "video.webm")
    trace_rel = _copy_if(evidence, meta.trace, rec, "trace.zip")

    placed_meta = RunMeta(
        id=meta.id, target=meta.target, created_at=meta.created_at,
        steps=placed, video=video_rel, trace=trace_rel, verdict=meta.verdict,
    )

    meta_json = run_dir / "meta.json"
    meta_json.write_text(json.dumps(placed_meta.to_dict(), indent=2), encoding="utf-8")
    report_md = run_dir / "report.md"
    report_md.write_text(render_inspection_report(placed_meta), encoding="utf-8")

    return PackagedRun(run_dir=run_dir, report_md=report_md, meta_json=meta_json)


def _copy_if(evidence: Path, src_name: str | None, dest_dir: Path, dst_name: str) -> str | None:
    """Copy ``evidence/<src_name>`` → ``dest_dir/<dst_name>``; return run-relative path."""
    if not src_name:
        return None
    src = evidence / src_name
    if not src.is_file():
        return None
    shutil.copy2(src, dest_dir / dst_name)
    return f"{dest_dir.name}/{dst_name}"


def finalize_run(run_dir, meta: RunMeta, *, generate=None, repo: str | None = None) -> None:
    """Write the P2 artifacts into an already-packaged run dir (#172):
    ``correction-plan.md`` (LLM seam + deterministic fallback) and ``issues.json``
    (the GitHub epic + child-per-failure specs, dry-run). Best-effort each.
    """
    import json as _json

    from .correction_plan import render_correction_plan
    from .issues import build_issue_specs, issue_specs_to_dict

    run_dir = Path(run_dir)
    (run_dir / "correction-plan.md").write_text(
        render_correction_plan(meta, generate=generate), encoding="utf-8"
    )
    epic, children = build_issue_specs(meta, repo=repo)
    (run_dir / "issues.json").write_text(
        _json.dumps(issue_specs_to_dict(epic, children), indent=2), encoding="utf-8"
    )
