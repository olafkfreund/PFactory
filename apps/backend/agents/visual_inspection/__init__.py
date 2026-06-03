"""Visual Inspection Run (#170).

Record a generated Playwright browser run, capture verification + error
screenshots, and package a human inspection report + recording into
``automated-test/<run-id>/``. P1 (#171) = the packaging + deterministic report;
P2 adds the LLM correction plan + GitHub export + downloads.
"""

from .correction_plan import build_correction_prompt, render_correction_plan
from .issues import (
    IssueSpec,
    build_issue_specs,
    issue_specs_to_dict,
    register_issues,
)
from .model import (
    RunMeta,
    StepResult,
    build_meta,
    new_run_id,
    slugify,
    verdict_for,
)
from .packager import PackagedRun, finalize_run, package_run
from .report import render_inspection_report

__all__ = [
    "StepResult",
    "RunMeta",
    "build_meta",
    "new_run_id",
    "slugify",
    "verdict_for",
    "PackagedRun",
    "package_run",
    "finalize_run",
    "render_inspection_report",
    "build_correction_prompt",
    "render_correction_plan",
    "IssueSpec",
    "build_issue_specs",
    "issue_specs_to_dict",
    "register_issues",
]
