"""Cloud assessment report + task-write (#133/#138).

Turns a discovery inventory + Prowler OCSF into the artifacts a PFactory task
attaches under ``<spec_dir>/findings/``:

    findings/cloud_assessment.md          — human report (verdict + tables)
    findings/cloud_assessment.json        — structured result (portal API / #140)
    findings/diagrams/cloud_topology.mmd  — the Mermaid service diagram

:func:`assess_and_write` is the seam the executor calls after the cloud runner
produces OCSF — pure apart from the final file writes, so it's fully testable.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from agents.diagrams import render_cloud_topology

from .assessment import CloudAssessment, CloudFinding, assess, parse_ocsf
from .issues import build_issue_specs, issue_specs_to_dict
from .remediation import render_remediation_plan

__all__ = [
    "assess_and_write",
    "cloud_findings_paths",
    "dedup_findings_for_diagram",
    "render_report_markdown",
]

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}


def cloud_findings_paths(spec_dir: Path) -> dict[str, Path]:
    """The three artifact paths under ``<spec_dir>/findings/`` (not created)."""
    base = Path(spec_dir) / "findings"
    return {
        "report_md": base / "cloud_assessment.md",
        "report_json": base / "cloud_assessment.json",
        "diagram_mmd": base / "diagrams" / "cloud_topology.mmd",
        "remediation_md": base / "cloud_remediation_plan.md",
        "issues_json": base / "cloud_issues.json",
    }


def dedup_findings_for_diagram(
    findings: list[CloudFinding], *, limit: int = 8
) -> list[dict]:
    """Collapse failing findings by (severity, scope, title) → ``title ×N``.

    Sorted worst-severity-then-count; capped at ``limit`` so the diagram stays
    legible. Returns the shape ``render_cloud_topology`` flags.
    """
    fails = [f for f in findings if f.status == "fail"]
    grp = Counter((f.severity, f.region or "global", f.title) for f in fails)
    items = sorted(grp.items(), key=lambda kv: (_SEV_ORDER.get(kv[0][0], 9), -kv[1]))
    out: list[dict] = []
    for (sev, scope, title), n in items[:limit]:
        label = title[:48] + (f"  ×{n}" if n > 1 else "")
        out.append({"severity": sev, "scope": scope, "title": label})
    return out


def render_report_markdown(
    inventory: dict, assessment: CloudAssessment, findings: list[CloudFinding]
) -> str:
    """Render the human-readable cloud assessment report."""
    a = assessment
    lines: list[str] = []
    lines.append(
        f"# PFactory Cloud Assessment — {str(inventory.get('provider', 'cloud')).upper()}"
    )
    acct = inventory.get("account", "?")
    ident = inventory.get("identity")
    sub = f"> Account `{acct}`"
    if ident:
        sub += f" · identity `{ident}`"
    sub += " · read-only run"
    lines.append("")
    lines.append(sub)
    lines.append("")
    lines.append(
        f"## Verdict: **{a.verdict.upper()}**  (gate: fail_on_severity = {a.fail_on_severity})"
    )
    lines.append("")
    lines.append(
        f"- Findings: **{a.total}** · ✅ pass {a.passed} · 🔴 fail {a.failed} · muted {a.muted}"
    )
    lines.append(f"- Fail severity counts: {dict(a.fail_counts)}")
    lines.append("")

    # Inventory
    g = inventory.get("global") or {}
    regions = inventory.get("regions") or {}
    if g or regions:
        lines.append("## Inventory")
        lines.append("| Scope | Resource | Count |")
        lines.append("|---|---|---|")
        if "s3" in g:
            s3 = g["s3"]
            note = f" {s3.get('note', '')}".rstrip()
            ok = " ✅" if s3.get("ok") else ""
            lines.append(f"| global | S3 buckets | {s3.get('count', '?')}{ok}{note} |")
        if "iam" in g:
            iam = g["iam"]
            lines.append(
                f"| global | IAM users/roles/policies | "
                f"{iam.get('users', '?')}/{iam.get('roles', '?')}/{iam.get('policies', '?')} |"
            )
        for r, rv in regions.items():
            lines.append(
                f"| {r} | VPC/EC2/Lambda | "
                f"{rv.get('vpcs', '-')}/{rv.get('instances', '-')}/{rv.get('lambdas', '-')} |"
            )
        lines.append("")

    # Top failing checks (deduped by severity+title with counts)
    fails = [f for f in findings if f.status == "fail"]
    if fails:
        grp = Counter((f.severity, f.title) for f in fails)
        rows = sorted(grp.items(), key=lambda kv: (_SEV_ORDER.get(kv[0][0], 9), -kv[1]))
        lines.append("## Top failing checks")
        lines.append("| Severity | Count | Check |")
        lines.append("|---|---|---|")
        for (sev, title), cnt in rows[:15]:
            lines.append(f"| {sev} | {cnt} | {title[:70]} |")
        lines.append("")

    lines.append("## Service topology")
    lines.append(
        "See `diagrams/cloud_topology.mmd` (renders in GitHub / any Mermaid viewer). Red nodes = findings."
    )
    lines.append("")
    lines.append("_Engine: Prowler (OCSF) → PFactory verdict._")
    return "\n".join(lines) + "\n"


def assess_and_write(
    spec_dir: Path,
    *,
    inventory: dict,
    ocsf: list | dict | str,
    fail_on_severity: str = "high",
    diagram_limit: int = 8,
) -> dict:
    """Assess ``ocsf`` and write the report + diagram + JSON into ``findings/``.

    Returns a summary ``{verdict, fail_counts, paths}``. Creates the
    ``findings/`` (and ``findings/diagrams/``) directories as needed.
    """
    findings = parse_ocsf(ocsf)
    a = assess(findings, fail_on_severity=fail_on_severity)

    inv = dict(inventory)
    inv["findings"] = dedup_findings_for_diagram(findings, limit=diagram_limit)
    diagram = render_cloud_topology(inv)
    report = render_report_markdown(inventory, a, findings)
    remediation = render_remediation_plan(findings, fail_on_severity=fail_on_severity)

    paths = cloud_findings_paths(spec_dir)
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    epic, children = build_issue_specs(
        findings,
        provider=str(inventory.get("provider", "cloud")),
        account=str(inventory.get("account", "?")),
        fail_on_severity=fail_on_severity,
    )

    paths["diagram_mmd"].write_text(diagram, encoding="utf-8")
    paths["report_md"].write_text(report, encoding="utf-8")
    paths["remediation_md"].write_text(remediation, encoding="utf-8")
    paths["issues_json"].write_text(
        json.dumps(issue_specs_to_dict(epic, children), indent=2), encoding="utf-8"
    )
    paths["report_json"].write_text(
        json.dumps(
            {
                "provider": inventory.get("provider"),
                "account": inventory.get("account"),
                "verdict": a.verdict,
                "fail_on_severity": a.fail_on_severity,
                "total": a.total,
                "passed": a.passed,
                "failed": a.failed,
                "muted": a.muted,
                "fail_counts": a.fail_counts,
                "findings": inv["findings"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "verdict": a.verdict,
        "fail_counts": a.fail_counts,
        "paths": {k: str(v) for k, v in paths.items()},
    }
