"""Deterministic visual-inspection report (#170 / P1 #171).

Pure string rendering from a ``RunMeta`` — no LLM, byte-stable for the same
input. The report is the human's view: a verdict header, per-step table with
inline screenshot thumbnails, and the captured errors annotated. The LLM
**correction plan** is a separate artifact (P2), not this file.
"""

from __future__ import annotations

from .model import RunMeta

__all__ = ["render_inspection_report"]

_VERDICT_BADGE = {
    "pass": "✅ PASS",
    "attention": "🟡 ATTENTION",
    "fail": "🔴 FAIL",
}
_STATE_ICON = {"pass": "✅", "fail": "🔴"}


def render_inspection_report(meta: RunMeta) -> str:
    """Render the human visual-inspection report as Markdown."""
    d = meta.to_dict()
    counts = d["counts"]
    target = d["target"]
    lines: list[str] = []

    lines.append(f"# Visual Inspection — {target.get('name', '?')}")
    lines.append("")
    badge = _VERDICT_BADGE.get(d["verdict"], d["verdict"].upper())
    lines.append(
        f"**Verdict:** {badge} · {counts['passed']}/{counts['steps']} steps passed"
        f" · {counts['failed']} failed"
    )
    bits = [f"`{target['name']}`"] if target.get("name") else []
    if target.get("platform"):
        bits.append(target["platform"])
    if target.get("base_url"):
        bits.append(target["base_url"])
    lines.append(f"**Target:** {' · '.join(bits)}")
    lines.append(f"**Run:** `{d['id']}` · {d['created_at']}")
    lines.append("")

    # ── per-step table ────────────────────────────────────────────────────
    lines.append("## Steps")
    lines.append("")
    lines.append("| # | Step | Result | Screenshot |")
    lines.append("|---|------|--------|------------|")
    for s in d["steps"]:
        icon = _STATE_ICON.get(s["state"], s["state"])
        shot = s.get("screenshot")
        cell = f"![{s['label']}]({shot})" if shot else "—"
        lines.append(f"| {s['n']} | {_md_escape(s['label'])} | {icon} {s['state']} | {cell} |")
    lines.append("")

    # ── failures / problems (for human tracking) ──────────────────────────
    failures = [s for s in d["steps"] if s["state"] == "fail"]
    if failures:
        lines.append("## Problems found")
        lines.append("")
        for s in failures:
            lines.append(f"### 🔴 Step {s['n']} — {_md_escape(s['label'])}")
            if s.get("error"):
                lines.append("")
                lines.append(f"> {_md_escape(s['error'])}")
            if s.get("screenshot"):
                lines.append("")
                lines.append(f"![error]({s['screenshot']})")
            lines.append("")
        lines.append(
            "_See `correction-plan.md` for recommendations + a fix plan, and "
            "`recording/trace.zip` (`npx playwright show-trace`) to replay the run._"
        )
    else:
        lines.append("## Problems found")
        lines.append("")
        lines.append("None — every verification step passed. ✅")

    lines.append("")
    return "\n".join(lines) + "\n"


def _md_escape(value: str) -> str:
    """Neutralise the few characters that break a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")
