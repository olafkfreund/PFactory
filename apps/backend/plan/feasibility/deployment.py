"""Deployment-readiness assessment (RFC-0013, #190).

The producing side of RFC-0013: turns what reconnaissance saw about the delivery
surface (CI system + deploy manifests on ``plan.repo_map``, the RFC-0010 blast
radius) plus best-effort DORA context into the additive ``deployment`` contract
block. It derives the risk/scan/system-gate POLICY (§3 of the RFC), records the
DRY-RUN deploy-verification policy (§6, production = VAL-4, never autonomous),
and produces the honest ``readiness`` checklist (§5, every non-pass carries a
reason).

It is **additive and back-compat**: when there is no deployment surface (no CI,
no deploy manifests, greenfield), :func:`assess_deployment_readiness` returns
``None`` and the contract simply carries no ``deployment`` block — old behavior.

Honesty rules (RFC-0013): a missing DORA context (``available: false``) means
delivery health is UNKNOWN, never healthy — the policy NEVER relaxes a gate on
the strength of missing metrics. Production is never deployed autonomously.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from plan.recon.delta import blast_radius, compute_footprints
from plan.recon.dora_client import dora_context
from plan.review.models import Citation, Finding

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.recon.models import RepoMap

_RFC_DOCS = (
    "https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0013-deployment-aware-planning.md"
)

# §3 floors — a planner may ADD scans/gates, never drop below these.
_SCAN_FLOOR: dict[str, list[str]] = {
    "low": ["secret-scan"],
    "medium": ["secret-scan", "sast", "dependency-audit"],
    "high": ["secret-scan", "sast", "dependency-audit", "iac-scan"],
}
_GATE_FLOOR: dict[str, list[str]] = {
    "low": ["ci-green"],
    "medium": ["ci-green"],
    "high": ["ci-green", "human-approval"],
}

# §6 — the DRY-RUN deploy-verification policy. The ladder CAPS at VAL-3 by
# design: there is no autonomous VAL-4 rung. Production stops at a plan/dry-run.
_VERIFICATION_BY_RISK: dict[str, dict[str, str]] = {
    "low": {"target_level": "VAL-2", "mode": "dry-run"},
    "medium": {"target_level": "VAL-3", "mode": "ephemeral-apply"},
    "high": {"target_level": "VAL-2", "mode": "plan-only"},
}

# The deployment acceptance criteria the user never writes (RFC-0013) — the
# delivery analogue of plan.decompose.implicit_requirements. Keyed so coverage is
# idempotent; keywords detect an equivalent AC the user already wrote.
_DEPLOY_ACS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "scans",
        "The required pre-deploy scans ({scans}) run in CI and pass.",
        ("scan", "sast", "dependency-audit", "secret-scan", "iac-scan"),
    ),
    (
        "rollback",
        "A rollback path is defined for the deploy (documented revert / "
        "previous-revision rollout).",
        ("rollback", "revert", "roll back", "previous revision"),
    ),
]
# Injected only when needs_pipeline (no usable CI exists for this change).
_PIPELINE_AC = (
    "pipeline",
    "A CI/CD pipeline is created/extended to build, scan, and deploy this change.",
    ("pipeline", "ci/cd", "workflow", "github actions", "gitlab-ci", "deploy job"),
)


def _has_deployment_surface(rm: RepoMap, radius: dict[str, Any]) -> bool:
    """True when there is any delivery dimension worth recording.

    A CI provider, a resolvable deploy mechanism, any deploy manifest, or a
    change that touches IaC destructively. Absent all of these the task has no
    deployment dimension (a library/docs change) and we emit no block.
    """
    if rm.ci_system and rm.ci_system != "none":
        return True
    if rm.deploy_system and rm.deploy_system not in ("none", None):
        return True
    if rm.deploy_manifests:
        return True
    return bool(radius.get("destructive_iac"))


def _edits_pipeline(rm: RepoMap, touched: list[str]) -> bool:
    """True when the change edits the CI/deploy pipeline itself (§3 high-risk)."""
    pipeline_paths = set(rm.ci_pipeline_paths or [])
    if pipeline_paths & set(touched):
        return True
    return any(".github/workflows/" in p or p.endswith(".gitlab-ci.yml") for p in touched)


def _derive_risk_class(
    rm: RepoMap, prod_class: str, radius: dict[str, Any], touched: list[str]
) -> str:
    """RFC-0013 §3 risk derivation — deterministic from the discovered surface.

    Reuses the RFC-0010 blast radius (``destructive_iac``) and the production
    classification. high > medium > low; missing DORA never lowers it.
    """
    destructive = bool(radius.get("destructive_iac"))
    if prod_class == "production" or destructive or _edits_pipeline(rm, touched):
        return "high"
    if prod_class in ("preprod", "staging") or rm.deploy_manifests:
        return "medium"
    return "low"


def _classify_production(environments: list[str]) -> str:
    """Highest-blast-radius class among the reachable environments."""
    if "production" in environments:
        return "production"
    if "preprod" in environments or "staging" in environments:
        return "preprod"
    if environments:
        return "internal"
    return "unknown"


def _build_readiness(
    *,
    ci_exists: bool,
    needs_pipeline: bool,
    required_scans: list[str],
    deploy_system: str,
    dora: dict[str, Any],
) -> list[dict[str, Any]]:
    """The honest deploy-readiness checklist (§5). Every non-pass carries a reason."""
    readiness: list[dict[str, Any]] = []

    # ci-pipeline-present
    if ci_exists and not needs_pipeline:
        readiness.append({"check": "ci-pipeline-present", "status": "pass"})
    else:
        readiness.append(
            {
                "check": "ci-pipeline-present",
                "status": "fail",
                "reason": "no usable CI pipeline exists for this change.",
                "action": "Create or extend a CI/CD pipeline that builds, scans, and deploys.",
            }
        )

    # scans-configured — not_run: PFactory plans the scans; CI must wire them.
    readiness.append(
        {
            "check": "scans-configured",
            "status": "not_run",
            "reason": (
                "required pre-deploy scans are planned but not yet verified present "
                f"in CI: {', '.join(required_scans)}."
            ),
            "action": "Wire the required scans into the deploy pipeline and assert they gate it.",
        }
    )

    # rollback-defined — not statically provable; honest not_run.
    readiness.append(
        {
            "check": "rollback-defined",
            "status": "not_run",
            "reason": "a rollback path could not be confirmed from static analysis.",
            "action": (
                f"Define a rollback for the {deploy_system} deploy "
                "(documented revert / previous-revision rollout)."
            ),
        }
    )

    # delivery-health-known — honest UNKNOWN when DORA is unavailable.
    if dora.get("available"):
        readiness.append({"check": "delivery-health-known", "status": "pass"})
    else:
        readiness.append(
            {
                "check": "delivery-health-known",
                "status": "not_run",
                "reason": (
                    "DORA delivery metrics unavailable ("
                    f"{dora.get('reason') or 'no provider'}) — delivery health is UNKNOWN, "
                    "not healthy."
                ),
                "action": "Wire a deployment-metrics MCP provider to surface delivery health.",
            }
        )
    return readiness


def assess_deployment_readiness(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    dora_fixture: dict[str, Any] | None = None,
    dora_mcp_client: Any | None = None,
) -> dict[str, Any] | None:
    """Produce the RFC-0013 ``deployment`` block, or ``None`` when not applicable.

    Returns ``None`` (no block) when reconnaissance is unavailable or there is no
    deployment surface — fully back-compat. Otherwise returns a schema-conforming
    ``deployment`` dict (the block shape in ``$defs.deployment``).

    Never raises: a degraded probe simply yields a low-risk, mostly-not_run block.
    """
    rm = plan.repo_map
    if rm is None or not rm.available:
        return None

    footprints = compute_footprints(plan, epic)
    radius = blast_radius(footprints, rm)
    touched = list(radius.get("files", []))

    if not _has_deployment_surface(rm, radius):
        return None

    ci_system = rm.ci_system or "none"
    ci_pipeline_paths = list(rm.ci_pipeline_paths or [])
    ci_exists = ci_system != "none" and bool(ci_pipeline_paths)
    deploy_system = rm.deploy_system or "none"

    # Environments come from the deploy-manifest probe; absent => unknown (never
    # fabricate a production target).
    environments = list(rm.deploy_environments or [])
    prod_class = _classify_production(environments)

    risk_class = _derive_risk_class(rm, prod_class, radius, touched)
    required_scans = list(_SCAN_FLOOR[risk_class])
    system_gates = list(_GATE_FLOOR[risk_class])
    # Production ALWAYS carries human-approval — never autonomous (§3 + §6).
    if prod_class == "production" and "human-approval" not in system_gates:
        system_gates.append("human-approval")

    # needs_pipeline: a deployable surface with no usable CI to ride.
    has_deploy = deploy_system not in ("none",) or bool(rm.deploy_manifests)
    needs_pipeline = has_deploy and not ci_exists

    dora = dora_context(
        rm.repo,
        environments[-1] if environments else None,
        mcp_client=dora_mcp_client,
        fixture=dora_fixture,
    )

    readiness = _build_readiness(
        ci_exists=ci_exists,
        needs_pipeline=needs_pipeline,
        required_scans=required_scans,
        deploy_system=deploy_system,
        dora=dora,
    )

    # §6 verification policy. Production forces plan-only (VAL-4 is never the
    # planned target; the real apply is held behind the human-approval gate).
    verification = dict(_VERIFICATION_BY_RISK[risk_class])
    if prod_class == "production":
        verification = {"target_level": "VAL-2", "mode": "plan-only"}

    block: dict[str, Any] = {
        "ci_system": ci_system,
        "ci_exists": ci_exists,
        "ci_pipeline_paths": ci_pipeline_paths,
        "needs_pipeline": needs_pipeline,
        "deploy_system": deploy_system,
        "deploy_target_environments": environments,
        "production_classification": prod_class,
        "risk_class": risk_class,
        "required_scans": required_scans,
        "system_gates": system_gates,
        "dora_context": dora,
        "readiness": readiness,
        "deploy_verification": verification,
    }
    return block


def deployment_acs(block: dict[str, Any]) -> list[tuple[str, str]]:
    """The implicit deployment acceptance criteria for a derived block.

    Returns ``(key, ac_text)`` pairs: the scans + rollback ACs always, plus a
    pipeline AC when ``needs_pipeline``. Resolved against the block's actual
    required scans so the text is concrete.
    """
    scans = ", ".join(block.get("required_scans", [])) or "secret-scan"
    out: list[tuple[str, str]] = [(key, text.format(scans=scans)) for key, text, _kw in _DEPLOY_ACS]
    if block.get("needs_pipeline"):
        key, text, _kw = _PIPELINE_AC
        out.insert(0, (key, text))
    return out


def _haystack(epic: EpicPlan) -> str:
    parts: list[str] = []
    for child in epic.children:
        parts.extend(ac.lower() for ac in child.acceptance_criteria)
        parts.append((child.body or "").lower())
    return "\n".join(parts)


def inject_deployment_acs(epic: EpicPlan, block: dict[str, Any]) -> list[str]:
    """Append any missing deployment ACs to the epic's primary child (idempotent).

    Coverage is keyword-based against existing child ACs/bodies so an equivalent
    criterion the user wrote isn't duplicated. Returns the injected texts.
    """
    if not epic.children:
        return []
    hay = _haystack(epic)
    keyword_map = {key: kw for key, _t, kw in (*_DEPLOY_ACS, _PIPELINE_AC)}
    injected: list[str] = []
    for key, text in deployment_acs(block):
        if any(kw in hay for kw in keyword_map.get(key, ())):
            continue
        injected.append(text)
    if not injected:
        return []
    target = next((c for c in epic.children if c.kind == "feature"), epic.children[0])
    target.acceptance_criteria = list(target.acceptance_criteria) + injected
    return injected


def deployment_findings(block: dict[str, Any]) -> list[Finding]:
    """Cited review findings summarizing the deployment policy.

    An info summary always; an advisory (non-blocking) finding when the change
    needs a pipeline or is high-risk/production so a human sees it at review.
    """
    findings: list[Finding] = []
    risk = block["risk_class"]
    prod = block["production_classification"]
    findings.append(
        Finding(
            title=(
                f"Deployment policy: {risk}-risk, deploys via {block['deploy_system']} to {prod}"
            ),
            detail=(
                f"Required scans: {', '.join(block['required_scans'])}. "
                f"System gates: {', '.join(block['system_gates'])}. "
                f"Deploy verification: {block['deploy_verification']['mode']} "
                f"({block['deploy_verification']['target_level']})."
            ),
            severity="info",
            source="deployment-readiness",
            citations=[
                Citation(
                    why="A change must be planned for how it ships, not only what it does.",
                    uri=_RFC_DOCS,
                    title="RFC-0013 Deployment-Aware Planning",
                    source="rfc-0013",
                )
            ],
        )
    )
    if prod == "production":
        findings.append(
            Finding(
                title="Production deploy is gated on human approval (never autonomous)",
                detail=(
                    "production_classification=production forces the human-approval "
                    "system gate and caps deploy verification at a dry-run/plan "
                    "(VAL-4 production apply is never run by the fleet)."
                ),
                severity="high",
                source="deployment-readiness",
                blocking=False,
                citations=[
                    Citation(
                        why="Production applies require explicit human authorization.",
                        uri=_RFC_DOCS,
                        title="RFC-0013 §6",
                        source="rfc-0013",
                    )
                ],
            )
        )
    if block.get("needs_pipeline"):
        findings.append(
            Finding(
                title="This change needs a CI/CD pipeline created or extended",
                detail=(
                    "A deployable surface was found but no usable CI pipeline exists "
                    "to build/scan/deploy it. The plan adds a pipeline acceptance "
                    "criterion and a deploy-readiness gap."
                ),
                severity="medium",
                source="deployment-readiness",
                blocking=False,
            )
        )
    return findings
