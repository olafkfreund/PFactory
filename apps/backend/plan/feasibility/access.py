"""Technical-access verification (Phase C).

Asks the question feasibility really hinges on: *can PFactory / the current
principal actually do this work?* For AWS it resolves the caller identity and
runs ``iam:SimulatePrincipalPolicy`` for the actions the plan implies; absent or
insufficient access becomes an ``AccessRequirement`` (granted True/False/None)
plus a cited, advisory finding. Azure/GCP are guarded best-effort.

Never raises and never hard-blocks — a missing permission routes the plan to
human review with a clear remediation, it doesn't override the engineer.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from plan.decompose.models import AccessRequirement
from plan.review.models import Citation, Finding

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# Detected service → representative IAM actions the plan would need.
_ACTION_HINTS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (re.compile(r"(?i)\beks\b|kubernetes|k8s"), "aws",
     ["eks:CreateCluster", "ec2:RunInstances", "iam:CreateRole"]),
    (re.compile(r"(?i)\brds\b|postgres|aurora"), "aws", ["rds:CreateDBInstance"]),
    (re.compile(r"(?i)\bs3\b|bucket"), "aws", ["s3:CreateBucket", "s3:PutObject"]),
    (re.compile(r"(?i)\belasticache\b|\bredis\b"), "aws", ["elasticache:CreateCacheCluster"]),
    (re.compile(r"(?i)\baks\b"), "azure", ["Microsoft.ContainerService/managedClusters/write"]),
    (re.compile(r"(?i)\bgke\b"), "gcp", ["container.clusters.create"]),
]

_IAM_DOCS = "https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_testing-policies.html"


def _plan_text(plan: NormalizedPlan) -> str:
    parts = [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
    return "\n".join(p for p in parts if p)


def required_actions(plan: NormalizedPlan) -> list[tuple[str, str]]:
    """List of (provider, action) the plan implies, de-duplicated."""
    text = _plan_text(plan)
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pat, provider, actions in _ACTION_HINTS:
        if pat.search(text):
            for action in actions:
                key = (provider, action)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


def _region(plan: NormalizedPlan) -> str:
    for entry in getattr(plan.enrichment, "infra", []) or []:
        if isinstance(entry, dict):
            regions = (entry.get("resources") or {}).get("regions") or []
            if regions:
                return str(regions[0])
    return ""


def verify_access(
    plan: NormalizedPlan,
    epic: EpicPlan | None = None,
    *,
    aws_simulator=None,
) -> tuple[list[AccessRequirement], list[Finding]]:
    """Verify the principal can perform the plan's implied actions.

    ``aws_simulator`` (test seam) is a callable ``(actions: list[str]) -> dict``
    mapping each action to ``"allowed"`` / ``"explicitDeny"`` / ``"implicitDeny"``.
    When omitted, a real IAM simulation is attempted via boto3 (guarded).
    """
    actions = required_actions(plan)
    if not actions:
        return [], []

    region = _region(plan)
    aws_actions = [a for prov, a in actions if prov == "aws"]
    other = [(prov, a) for prov, a in actions if prov != "aws"]

    reqs: list[AccessRequirement] = []
    findings: list[Finding] = []

    # ── AWS: real IAM policy-simulation (or injected simulator) ──
    sim_result = None
    if aws_actions:
        sim_result = _run_aws_simulation(aws_actions, aws_simulator)
    if aws_actions and sim_result is None:
        # Couldn't verify (no creds / SDK) — advise, don't fail.
        for action in aws_actions:
            reqs.append(AccessRequirement(provider="aws", action=action, region=region, granted=None))
        findings.append(
            Finding(
                title="AWS access not verified — credentials/permissions unknown",
                detail=(
                    "PFactory couldn't reach AWS IAM to simulate the actions this "
                    f"plan needs ({', '.join(aws_actions)}). Confirm the executing "
                    "principal has them before handing off."
                ),
                severity="medium",
                source="feasibility-access",
                blocking=False,
                citations=[Citation(why="IAM policy simulation verifies the principal can perform the work.",
                                    uri=_IAM_DOCS, title="Testing IAM policies", source="aws-iam")],
            )
        )
    elif aws_actions:
        for action in aws_actions:
            decision = sim_result.get(action, "implicitDeny")
            granted = decision == "allowed"
            reqs.append(AccessRequirement(provider="aws", action=action, region=region, granted=granted))
            if not granted:
                findings.append(
                    Finding(
                        title=f"Principal cannot {action}",
                        detail=(
                            f"IAM simulation returned '{decision}' for {action}. Grant it "
                            "to the executing principal (or scope the plan) before handoff."
                        ),
                        severity="high",
                        source="feasibility-access",
                        blocking=False,
                        citations=[Citation(why="The plan requires this action to be built as described.",
                                            uri=_IAM_DOCS, title="Testing IAM policies", source="aws-iam")],
                    )
                )

    # ── Azure / GCP: guarded best-effort (not verified here yet) ──
    for prov, action in other:
        reqs.append(AccessRequirement(provider=prov, action=action, region=region, granted=None))
    if other:
        provs = sorted({p for p, _ in other})
        findings.append(
            Finding(
                title=f"{'/'.join(provs).upper()} access not verified",
                detail=(
                    "Access-simulation for "
                    f"{', '.join(f'{p}:{a}' for p, a in other)} isn't wired yet; "
                    "confirm the principal's roles manually."
                ),
                severity="low",
                source="feasibility-access",
                blocking=False,
                citations=[Citation(why="The plan targets these clouds; their permissions must be confirmed.",
                                    uri="", title="", source=provs[0])],
            )
        )

    return reqs, findings


def _run_aws_simulation(actions: list[str], simulator) -> dict | None:
    """Return {action: decision} via the injected simulator or real boto3 IAM."""
    if simulator is not None:
        try:
            return simulator(actions)
        except Exception:
            return None
    try:  # pragma: no cover - exercised only with live AWS creds
        import boto3

        session = boto3.Session()
        arn = session.client("sts").get_caller_identity()["Arn"]
        iam = session.client("iam")
        resp = iam.simulate_principal_policy(PolicySourceArn=arn, ActionNames=actions)
        return {
            r["EvalActionName"]: r["EvalDecision"]
            for r in resp.get("EvaluationResults", [])
        }
    except Exception:
        return None
