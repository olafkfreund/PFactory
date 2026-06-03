"""Tests for cloud OCSF → verdict mapping (#133/#138).

Backend-pure: parses canned OCSF records shaped like real Prowler v5 output.
"""

from __future__ import annotations

from agents.cloud.assessment import (
    assess,
    parse_ocsf,
    to_inventory_findings,
)


def _rec(status="FAIL", severity="High", title="t", region="us-east-1", check="iam_x"):
    return {
        "status_code": status,
        "severity": severity,
        "finding_info": {"title": title, "uid": f"prowler-aws-{check}-123"},
        "metadata": {"event_code": check},
        "resources": [{"name": "res-1", "region": region, "uid": "arn:aws:..."}],
        "cloud": {"region": region, "provider": "aws", "account": {"uid": "533267307120"}},
    }


# ── parse_ocsf ───────────────────────────────────────────────────────────────


def test_parse_extracts_fields() -> None:
    [f] = parse_ocsf([_rec(severity="Critical", title="Public bucket", check="s3_pub")])
    assert f.severity == "critical"
    assert f.status == "fail"
    assert f.title == "Public bucket"
    assert f.region == "us-east-1"
    assert "s3_pub" in f.check_id


def test_parse_accepts_json_string_and_single_dict() -> None:
    import json

    assert len(parse_ocsf(json.dumps([_rec(), _rec()]))) == 2
    assert len(parse_ocsf(_rec())) == 1


def test_parse_falls_back_to_severity_id() -> None:
    rec = _rec()
    del rec["severity"]
    rec["severity_id"] = 4  # → high
    [f] = parse_ocsf([rec])
    assert f.severity == "high"


def test_parse_tolerates_missing_region_and_resources() -> None:
    rec = {"status_code": "FAIL", "severity": "Low", "finding_info": {"title": "x"}}
    [f] = parse_ocsf([rec])
    assert f.region is None and f.resource is None and f.severity == "low"


# ── assess: the verdict gate ─────────────────────────────────────────────────


def test_assess_reject_on_high_at_default_gate() -> None:
    a = assess(parse_ocsf([_rec(severity="High"), _rec(status="PASS")]))
    assert a.verdict == "reject"
    assert a.failed == 1 and a.passed == 1
    assert a.fail_counts == {"high": 1}


def test_assess_flag_when_fails_below_gate() -> None:
    # only medium/low fails, gate=high → flag (not reject)
    a = assess(parse_ocsf([_rec(severity="Medium"), _rec(severity="Low")]))
    assert a.verdict == "flag"
    assert a.failed == 2


def test_assess_accept_when_no_fails() -> None:
    a = assess(parse_ocsf([_rec(status="PASS"), _rec(status="MUTED")]))
    assert a.verdict == "accept"
    assert a.passed == 1 and a.muted == 1 and a.failed == 0


def test_assess_gate_is_configurable() -> None:
    findings = parse_ocsf([_rec(severity="Medium")])
    assert assess(findings, fail_on_severity="medium").verdict == "reject"
    assert assess(findings, fail_on_severity="high").verdict == "flag"
    assert assess(findings, fail_on_severity="critical").verdict == "flag"


def test_assess_critical_breaches_high_gate() -> None:
    a = assess(parse_ocsf([_rec(severity="Critical")]), fail_on_severity="high")
    assert a.verdict == "reject"


# ── to_inventory_findings (diagram) ──────────────────────────────────────────


def test_inventory_findings_sorted_worst_first_and_scoped() -> None:
    findings = parse_ocsf([
        _rec(severity="Low", title="low one", region="eu-west-2"),
        _rec(severity="Critical", title="crit one", region="us-east-1"),
        _rec(status="PASS", severity="High"),  # passes are excluded
    ])
    out = to_inventory_findings(findings)
    assert [f["title"] for f in out] == ["crit one", "low one"]  # critical first
    assert out[0]["scope"] == "us-east-1"
    assert all(f["severity"] in {"critical", "low"} for f in out)


def test_inventory_findings_limit() -> None:
    findings = parse_ocsf([_rec(check=f"c{i}") for i in range(30)])
    assert len(to_inventory_findings(findings, limit=5)) == 5


def test_inventory_findings_feeds_diagram() -> None:
    from agents.diagrams import render_cloud_topology

    findings = parse_ocsf([_rec(severity="High", title="15/18 users no MFA", region=None)])
    inv = {"provider": "aws", "account": "1", "findings": to_inventory_findings(findings)}
    out = render_cloud_topology(inv)
    assert "🔴 15/18 users no MFA (high)" in out
