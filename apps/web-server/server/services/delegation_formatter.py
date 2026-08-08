"""Render the PFactory-enriched plan as a GitHub issue comment.

Used by the delegation flow in ``auto_fix_service.start_auto_fix`` to
hand Copilot a structured, fully-contextualised plan instead of the
raw issue body. The comment is the only thing Copilot sees besides
the original issue.

Output shape (markdown):
    ## :sparkles: PFactory enrichment
    ### Spec
    ...spec.md body, trimmed...
    ### Acceptance criteria
    ...derived from test_plan.json subtasks...
    ### Affected files
    ...derived from test_plan.json...
    ### Implementation plan
    ...subtasks grouped by phase...
    ---
    _PFactory drafted this plan. @Copilot — please implement._
"""

from __future__ import annotations

import json
from pathlib import Path


def render_plan_as_comment(plan_json_path: Path, spec_md_path: Path) -> str:
    """Build the structured comment body posted on the GitHub issue.

    Both inputs are read defensively — if a file is missing or malformed,
    the corresponding section degrades to a single explanatory line rather
    than raising. That way a partial plan still produces a usable comment.
    """
    spec_body = _read_spec_body(spec_md_path)
    plan = _read_plan(plan_json_path)

    parts: list[str] = ["## ✨ PFactory enrichment", ""]

    parts.append("### Spec")
    parts.append("")
    parts.append(spec_body or "_Spec body unavailable._")
    parts.append("")

    parts.append("### Acceptance criteria")
    parts.append("")
    criteria = _collect_acceptance_criteria(plan)
    if criteria:
        parts.extend(f"- [ ] {c}" for c in criteria)
    else:
        parts.append("_No explicit acceptance criteria listed in the plan._")
    parts.append("")

    parts.append("### Affected files")
    parts.append("")
    files = _collect_affected_files(plan)
    if files:
        parts.extend(f"- `{f}`" for f in files)
    else:
        parts.append("_No affected files listed in the plan._")
    parts.append("")

    parts.append("### Implementation plan")
    parts.append("")
    phases = _group_by_phase(plan)
    if phases:
        for phase_name, subtasks in phases:
            parts.append(f"**{phase_name}**")
            parts.append("")
            for st in subtasks:
                desc = st.get("description") or st.get("title") or "(no description)"
                parts.append(f"- {desc}")
            parts.append("")
    else:
        parts.append("_Plan structure unavailable._")
        parts.append("")

    parts.append("---")
    parts.append("_PFactory drafted this plan. @Copilot — please implement._")
    return "\n".join(parts)


def _read_spec_body(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""
    # Drop the first H1 line so it doesn't duplicate the issue's title.
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _read_plan(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _collect_acceptance_criteria(plan: dict) -> list[str]:
    out: list[str] = []
    # Plan-level criteria (most common case).
    plan_level = plan.get("acceptance_criteria") or plan.get("acceptanceCriteria")
    if isinstance(plan_level, list):
        out.extend(str(c) for c in plan_level if c)
    # Per-subtask criteria fall in as fallback if the plan has no top-level set.
    if not out:
        for subtask in _iter_subtasks(plan):
            sub_crit = subtask.get("acceptance_criteria") or subtask.get("acceptanceCriteria")
            if isinstance(sub_crit, list):
                out.extend(str(c) for c in sub_crit if c)
    # Dedupe preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


def _collect_affected_files(plan: dict) -> list[str]:
    out: list[str] = []
    for subtask in _iter_subtasks(plan):
        files = subtask.get("affected_files") or subtask.get("affectedFiles") or []
        if isinstance(files, list):
            out.extend(str(f) for f in files if f)
    seen: set[str] = set()
    deduped: list[str] = []
    for f in out:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


def _group_by_phase(plan: dict) -> list[tuple[str, list[dict]]]:
    """Return [(phase_name, [subtask, ...]), ...] preserving original order."""
    phases = plan.get("phases")
    if isinstance(phases, list) and phases:
        out = []
        for phase in phases:
            name = phase.get("name") or phase.get("phase_name") or "Phase"
            subs = phase.get("subtasks") or []
            if isinstance(subs, list):
                out.append((str(name), [s for s in subs if isinstance(s, dict)]))
        return out
    # Flat subtask list fallback — treat all subtasks as one phase.
    flat = plan.get("subtasks")
    if isinstance(flat, list) and flat:
        return [("Implementation", [s for s in flat if isinstance(s, dict)])]
    return []


def _iter_subtasks(plan: dict):
    """Yield every subtask dict regardless of whether the plan is phased or flat."""
    phases = plan.get("phases")
    if isinstance(phases, list):
        for phase in phases:
            for st in phase.get("subtasks") or []:
                if isinstance(st, dict):
                    yield st
    flat = plan.get("subtasks")
    if isinstance(flat, list):
        for st in flat:
            if isinstance(st, dict):
                yield st
