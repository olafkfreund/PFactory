"""Cloud discovery + assessment for PFactory (#133).

Read-only by design: enumerate a cloud account's identity + resources into a
normalized inventory (consumed by ``agents.diagrams.render_cloud_topology`` and
the cloud assessment framework). Never mutates the account.
"""

from .assessment import (
    CloudAssessment,
    CloudFinding,
    assess,
    parse_ocsf,
    to_inventory_findings,
)
from .discovery import AccessResult, access_check, discover
from .issues import build_issue_specs, issue_specs_to_dict, register_issues
from .remediation import render_remediation_plan
from .report import assess_and_write, cloud_findings_paths, render_report_markdown
from .runner import build_prowler_command, run_cloud_assessment

__all__ = [
    "AccessResult",
    "CloudAssessment",
    "CloudFinding",
    "access_check",
    "assess",
    "assess_and_write",
    "build_issue_specs",
    "build_prowler_command",
    "cloud_findings_paths",
    "discover",
    "issue_specs_to_dict",
    "parse_ocsf",
    "register_issues",
    "render_remediation_plan",
    "render_report_markdown",
    "run_cloud_assessment",
    "to_inventory_findings",
]
