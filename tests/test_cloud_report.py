"""Tests for cloud assessment report + task-write (#133/#138).

Backend-pure: writes into a tmp_path workspace; no cloud, no Prowler.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.cloud.assessment import assess, parse_ocsf
from agents.cloud.report import (
    assess_and_write,
    cloud_findings_paths,
    dedup_findings_for_diagram,
    render_report_markdown,
)

_INV = {
    "provider": "aws",
    "account": "533267307120",
    "identity": "Olaf.Freund",
    "global": {
        "s3": {"count": 12, "ok": True, "note": "PAB-blocked + AES256"},
        "iam": {"users": 18, "roles": 121, "policies": 113},
    },
    "regions": {"eu-west-2": {"vpcs": 1, "instances": 11, "lambdas": 0}},
}


def _rec(status="FAIL", severity="High", title="EBS volume is encrypted", region="eu-west-2"):
    return {
        "status_code": status,
        "severity": severity,
        "finding_info": {"title": title, "uid": f"prowler-aws-{title[:6]}-1"},
        "resources": [{"name": "r", "region": region}],
        "cloud": {"region": region},
    }


# ── paths ────────────────────────────────────────────────────────────────────


def test_findings_paths_layout(tmp_path: Path) -> None:
    p = cloud_findings_paths(tmp_path)
    assert p["report_md"] == tmp_path / "findings" / "cloud_assessment.md"
    assert p["report_json"] == tmp_path / "findings" / "cloud_assessment.json"
    assert p["diagram_mmd"] == tmp_path / "findings" / "diagrams" / "cloud_topology.mmd"


# ── dedup ────────────────────────────────────────────────────────────────────


def test_dedup_collapses_duplicates_with_count() -> None:
    findings = parse_ocsf([_rec() for _ in range(9)] + [_rec(severity="Critical", title="Public bucket")])
    out = dedup_findings_for_diagram(findings, limit=8)
    assert out[0]["severity"] == "critical"  # worst first
    ebs = [f for f in out if "EBS volume is encrypted" in f["title"]][0]
    assert "×9" in ebs["title"]


# ── render ───────────────────────────────────────────────────────────────────


def test_render_report_has_verdict_inventory_and_checks() -> None:
    findings = parse_ocsf([_rec(), _rec(status="PASS")])
    a = assess(findings)
    md = render_report_markdown(_INV, a, findings)
    assert "Verdict: **REJECT**" in md
    assert "Account `533267307120`" in md
    assert "S3 buckets | 12 ✅" in md
    assert "eu-west-2 | VPC/EC2/Lambda | 1/11/0" in md
    assert "Top failing checks" in md


# ── assess_and_write ─────────────────────────────────────────────────────────


def test_assess_and_write_creates_all_artifacts(tmp_path: Path) -> None:
    ocsf = [_rec(severity="High"), _rec(severity="Medium"), _rec(status="PASS")]
    result = assess_and_write(tmp_path, inventory=_INV, ocsf=ocsf, fail_on_severity="high")

    assert result["verdict"] == "reject"
    p = cloud_findings_paths(tmp_path)
    assert p["report_md"].is_file()
    assert p["diagram_mmd"].is_file()
    assert p["report_json"].is_file()

    # report content
    assert "REJECT" in p["report_md"].read_text()
    # diagram has the topology + a flagged finding
    diag = p["diagram_mmd"].read_text()
    assert diag.startswith("graph LR\n") and "🔴" in diag and "class " in diag
    # json is structured + matches
    data = json.loads(p["report_json"].read_text())
    assert data["verdict"] == "reject"
    assert data["account"] == "533267307120"
    assert data["failed"] == 2 and data["passed"] == 1
    assert isinstance(data["findings"], list) and data["findings"]


def test_assess_and_write_accept_when_clean(tmp_path: Path) -> None:
    result = assess_and_write(
        tmp_path, inventory=_INV, ocsf=[_rec(status="PASS")], fail_on_severity="high"
    )
    assert result["verdict"] == "accept"
    assert cloud_findings_paths(tmp_path)["report_md"].is_file()


def test_assess_and_write_respects_gate(tmp_path: Path) -> None:
    # only medium fails; gate=high → flag (not reject)
    result = assess_and_write(
        tmp_path, inventory=_INV, ocsf=[_rec(severity="Medium")], fail_on_severity="high"
    )
    assert result["verdict"] == "flag"
