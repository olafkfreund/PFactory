"""Visual Inspection correction plan (#170 / P2 #172).

The one LLM-touched artifact: given a run's failures (error screenshots +
messages + the step it broke on), produce **recommendations + a correction plan**
that's AiFactory-task-ready — i.e. a reviewer can hand `correction-plan.md`
straight to a coding agent as the task.

The LLM is an **injectable seam** (`generate`) so tests run without an SDK, and
there is a **deterministic fallback** when no generator is wired (or it fails) —
the plan always renders. Mirrors the evaluator's LLM-with-fallback split.
"""

from __future__ import annotations

from typing import Callable

from .model import RunMeta

__all__ = ["build_correction_prompt", "render_correction_plan"]


def _failures(meta: RunMeta) -> list:
    return [s for s in meta.steps if s.state == "fail"]


def build_correction_prompt(meta: RunMeta) -> str:
    """Assemble the deterministic prompt fed to the LLM (no secrets, no images)."""
    d = meta.to_dict()
    fails = _failures(meta)
    lines = [
        "You are a senior QA + frontend engineer reviewing an automated visual "
        "inspection of a web UI. Write a concise **correction plan** in Markdown "
        "that another engineer (or a coding agent) can act on directly.",
        "",
        f"Target: {d['target'].get('name')} ({d['target'].get('platform', 'web')}) "
        f"— {d['target'].get('base_url', '')}",
        f"Verdict: {d['verdict']} — {d['counts']['failed']} of {d['counts']['steps']} "
        "steps failed.",
        "",
        "Failed steps:",
    ]
    for s in fails:
        lines.append(f"- Step {s.n} \"{s.label}\": {s.error or 'assertion failed'}")
    lines += [
        "",
        "For each failure give: the likely root cause, the concrete fix, and how to "
        "verify it. End with a short prioritised task list a coding agent could "
        "execute. Do not invent UI details not implied by the failures.",
    ]
    return "\n".join(lines)


def _deterministic_plan(meta: RunMeta) -> str:
    """Fallback plan assembled from the failures — no LLM."""
    d = meta.to_dict()
    fails = _failures(meta)
    out = [
        f"# Correction plan — {d['target'].get('name', '?')}",
        "",
        f"_Generated deterministically (no model). Verdict: **{d['verdict']}** · "
        f"{d['counts']['failed']}/{d['counts']['steps']} steps failed._",
        "",
    ]
    if not fails:
        out += ["No failing steps — nothing to correct. ✅", ""]
        return "\n".join(out) + "\n"
    out.append("## Findings")
    out.append("")
    for s in fails:
        out += [
            f"### Step {s.n} — {s.label}",
            "",
            f"- **What broke:** {s.error or 'the step assertion failed'}",
            f"- **Where:** verification step {s.n} (\"{s.label}\"); "
            f"see `screenshots/` + `recording/trace.zip`.",
            "- **Likely cause:** a changed/missing element, a slow async update, or "
            "a regression in this step's behaviour.",
            "- **Fix:** reproduce via the trace, correct the UI/flow (or the "
            "assertion if the expectation is wrong), and re-run.",
            "",
        ]
    out += [
        "## Suggested task (AIFactory-ready)",
        "",
        "> Fix the failing visual-inspection steps above. For each: reproduce from "
        "`recording/trace.zip`, address the root cause, and confirm the step "
        "passes on a re-run. Attach updated screenshots.",
        "",
    ]
    return "\n".join(out) + "\n"


def render_correction_plan(
    meta: RunMeta, *, generate: Callable[[str], str] | None = None
) -> str:
    """Render the correction plan. Uses ``generate(prompt)`` (the LLM seam) when
    provided + non-empty; otherwise the deterministic fallback. Never raises —
    a failing/empty generator degrades to the fallback so the artifact always
    lands.
    """
    if generate is not None:
        try:
            text = generate(build_correction_prompt(meta))
            if text and text.strip():
                return text if text.endswith("\n") else text + "\n"
        except Exception:  # noqa: BLE001 - never break packaging on the LLM
            pass
    return _deterministic_plan(meta)
