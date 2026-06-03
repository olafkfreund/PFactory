"""Cloud remediation plan (#133/#150).

The assessment report says *what* is faulty; this turns the findings into a
prioritised **fix plan** — *how* and *what* to remediate — using Prowler's
per-check remediation guidance (``remediation.desc`` + references) and risk
context already captured on each :class:`CloudFinding`.

Deterministic: it aggregates and orders the findings' own guidance. (An LLM
"remediation campaign" summary is a possible future enhancement.)
"""

from __future__ import annotations

from collections import OrderedDict

from .assessment import CloudFinding

__all__ = ["render_remediation_plan"]

# Severity tiers, worst first, with a display heading.
_TIERS = [
    ("critical", "🔴 Critical — fix immediately"),
    ("high", "🔴 High — fix first"),
    ("medium", "🟠 Medium — schedule"),
    ("low", "🟡 Low — backlog"),
    ("informational", "ℹ️ Informational"),
]
_TIER_ORDER = {name: i for i, (name, _) in enumerate(_TIERS)}


def _group_fails(findings: list[CloudFinding]) -> OrderedDict[str, dict]:
    """Group failing findings by check → one remediation item per check.

    Ordered worst-severity-then-most-affected. Each item aggregates the affected
    resource count and keeps the first non-empty remediation / risk / references.
    """
    by_check: dict[str, dict] = {}
    for f in findings:
        if f.status != "fail":
            continue
        item = by_check.get(f.check_id)
        if item is None:
            item = {
                "check_id": f.check_id,
                "title": f.title,
                "severity": f.severity,
                "count": 0,
                "regions": set(),
                "resources": [],
                "remediation": f.remediation,
                "risk": f.risk,
                "references": list(f.references),
            }
            by_check[f.check_id] = item
        item["count"] += 1
        if f.region:
            item["regions"].add(f.region)
        if f.resource and len(item["resources"]) < 5:
            item["resources"].append(f.resource)
        # backfill guidance from any finding in the group that has it
        if not item["remediation"] and f.remediation:
            item["remediation"] = f.remediation
        if not item["risk"] and f.risk:
            item["risk"] = f.risk
        if not item["references"] and f.references:
            item["references"] = list(f.references)

    ordered = sorted(
        by_check.values(),
        key=lambda it: (_TIER_ORDER.get(it["severity"], 9), -it["count"]),
    )
    return OrderedDict((it["check_id"], it) for it in ordered)


def render_remediation_plan(
    findings: list[CloudFinding], *, fail_on_severity: str = "high"
) -> str:
    """Render a prioritised remediation plan (Markdown) from the findings."""
    items = list(_group_fails(findings).values())
    lines: list[str] = ["# Cloud Remediation Plan", ""]
    if not items:
        lines.append("✅ No failing checks — nothing to remediate.")
        return "\n".join(lines) + "\n"

    total = sum(it["count"] for it in items)
    lines.append(
        f"> {len(items)} issue type(s) across {total} finding(s) · "
        f"gate: fail_on_severity = {fail_on_severity}"
    )
    lines.append("")
    lines.append("Prioritised worst-first. Each fix is from Prowler's CIS guidance.")
    lines.append("")

    n = 0
    for sev, heading in _TIERS:
        tier = [it for it in items if it["severity"] == sev]
        if not tier:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for it in tier:
            n += 1
            regions = ", ".join(sorted(it["regions"])) if it["regions"] else "global"
            lines.append(f"### {n}. {it['title']}")
            lines.append(
                f"_{it['severity']} · {it['count']} affected · {regions}_"
            )
            if it["resources"]:
                more = "" if it["count"] <= len(it["resources"]) else " …"
                lines.append(f"- **Affected:** {', '.join(it['resources'])}{more}")
            if it["risk"]:
                lines.append(f"- **Risk:** {it['risk']}")
            if it["remediation"]:
                lines.append(f"- **Fix:** {it['remediation']}")
            for ref in it["references"][:3]:
                lines.append(f"- **Ref:** {ref}")
            lines.append("")
    return "\n".join(lines) + "\n"
