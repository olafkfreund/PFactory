"""Tests for the cloud remediation plan (#133/#150)."""

from __future__ import annotations

from pathlib import Path

from agents.cloud.assessment import parse_ocsf
from agents.cloud.remediation import render_remediation_plan
from agents.cloud.report import assess_and_write, cloud_findings_paths


def _rec(status="FAIL", severity="High", title="MFA disabled", check="iam_mfa",
         region="us-east-1", desc="Enable MFA for all users.", risk="Account takeover.",
         refs=("https://hub.prowler.com/check/iam_mfa",)):
    return {
        "status_code": status,
        "severity": severity,
        "finding_info": {"title": title, "uid": f"prowler-aws-{check}-1"},
        "metadata": {"event_code": check},
        "resources": [{"name": "user-a", "region": region}],
        "cloud": {"region": region},
        "remediation": {"desc": desc, "references": list(refs)},
        "risk_details": risk,
    }


# ── parse captures remediation ───────────────────────────────────────────────


def test_parse_captures_remediation_risk_refs() -> None:
    [f] = parse_ocsf([_rec()])
    assert f.remediation == "Enable MFA for all users."
    assert f.risk == "Account takeover."
    assert f.references == ("https://hub.prowler.com/check/iam_mfa",)


# ── render_remediation_plan ──────────────────────────────────────────────────


def test_plan_includes_fix_risk_and_refs() -> None:
    plan = render_remediation_plan(parse_ocsf([_rec()]))
    assert "# Cloud Remediation Plan" in plan
    assert "**Fix:** Enable MFA for all users." in plan
    assert "**Risk:** Account takeover." in plan
    assert "hub.prowler.com/check/iam_mfa" in plan


def test_plan_dedups_by_check_with_count() -> None:
    # 3 findings of the same check across resources → one item, "3 affected"
    recs = [_rec(region="us-east-1"), _rec(region="us-east-1"), _rec(region="eu-west-2")]
    plan = render_remediation_plan(parse_ocsf(recs))
    assert plan.count("### 1.") == 1
    assert "3 affected" in plan


def test_plan_orders_critical_before_high_before_medium() -> None:
    recs = [
        _rec(severity="Medium", title="ZZmedZZ", check="c_med"),
        _rec(severity="Critical", title="ZZcritZZ", check="c_crit"),
        _rec(severity="High", title="ZZhighZZ", check="c_hi"),
    ]
    plan = render_remediation_plan(parse_ocsf(recs))
    assert plan.index("ZZcritZZ") < plan.index("ZZhighZZ") < plan.index("ZZmedZZ")
    assert "🔴 Critical" in plan and "🟠 Medium" in plan


def test_plan_empty_when_no_fails() -> None:
    plan = render_remediation_plan(parse_ocsf([_rec(status="PASS")]))
    assert "nothing to remediate" in plan


# ── assess_and_write writes the plan ─────────────────────────────────────────


def test_assess_and_write_emits_remediation_plan(tmp_path: Path) -> None:
    assess_and_write(
        tmp_path,
        inventory={"provider": "aws", "account": "1"},
        ocsf=[_rec(severity="High")],
        fail_on_severity="high",
    )
    plan = cloud_findings_paths(tmp_path)["remediation_md"]
    assert plan.is_file()
    text = plan.read_text()
    assert "Cloud Remediation Plan" in text and "**Fix:**" in text
