"""Assemble + emit the full signed Task Contract (epic #65, child 8 transport).

:func:`assemble_contract` composes every block built by the other #65 children
(plan → execution → review_tier → verification → tfactory) into one contract.
:func:`emit_contract` validates it, signs it (HMAC trusted_plan envelope), and —
unless dry-run — POSTs it to AIFactory's ``/api/tasks/from-plan`` skip-planning
endpoint, falling back to the legacy ``create-and-run`` requirements path if the
fast-path is unavailable. Dry-run by default, honouring the no-automatic-pushes
policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from plan.emit.contract_builder import build_task_contract
from plan.emit.execution_profile import attach_execution
from plan.emit.handoff_sanitize import attach_constraints
from plan.emit.review_tier import attach_review_tier
from plan.emit.signing import attach_signature, key_from_env
from plan.emit.task_contract import validate_contract
from plan.emit.tfactory_block import attach_tfactory
from plan.emit.verification import attach_verification

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.emit.aifactory_handoff import HttpClient
    from plan.models import NormalizedPlan
    from plan.review.models import PlanReview


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_plan_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/tasks/from-plan"


def _response_body(resp: Any) -> Any:
    """Best-effort extract a JSON body from an httpx/requests-style response."""
    if resp is None:
        return None
    getter = getattr(resp, "json", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return resp


def assemble_contract(
    plan: NormalizedPlan,
    epic: EpicPlan,
    review: PlanReview | None = None,
    *,
    repo: str | None = None,
    correlation_key: str | None = None,
) -> dict[str, Any]:
    """Compose the complete (unsigned) Task Contract from all #65 blocks."""
    contract = build_task_contract(plan, epic, repo=repo, correlation_key=correlation_key)
    attach_execution(contract, plan, epic)
    if review is not None:
        attach_review_tier(contract, review)
    attach_verification(contract, plan, epic)
    attach_tfactory(contract, plan, epic)
    # Carry sanitized live-cloud enrichment as epic_context constraints (#80).
    attach_constraints(contract, plan)
    return contract


def emit_contract(
    plan: NormalizedPlan,
    epic: EpicPlan,
    review: PlanReview | None = None,
    *,
    base_url: str,
    project_id: str,
    http: HttpClient | None = None,
    key: str | None = None,
    approval_timestamp: str | None = None,
    repo: str | None = None,
    correlation_key: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Assemble, validate, sign, and (unless dry-run) emit the contract.

    Returns a result dict: ``{ok, dry_run, signed, endpoint, contract, ...}``.
    On a validation failure ``ok`` is False and ``errors`` lists the problems —
    an incomplete contract is never POSTed. Live emit POSTs to ``/from-plan`` and
    falls back to ``create-and-run`` (first child) if that raises.
    """
    contract = assemble_contract(
        plan, epic, review, repo=repo, correlation_key=correlation_key
    )
    errors = validate_contract(contract)
    if errors:
        return {"ok": False, "dry_run": dry_run, "errors": errors, "contract": contract}

    signing_key = key or key_from_env("pfactory")
    signed = False
    if signing_key:
        attach_signature(
            contract, key=signing_key, approval_timestamp=approval_timestamp or _utcnow_iso()
        )
        signed = True

    url = _from_plan_url(base_url)
    if dry_run:
        return {"ok": True, "dry_run": True, "signed": signed, "endpoint": url, "contract": contract}

    if http is None:
        raise ValueError("live emit_contract requires an injected `http` client")

    try:
        resp = http.post(url, params={"project_id": project_id}, json=contract)
        return {
            "ok": True, "dry_run": False, "signed": signed, "endpoint": url,
            "fallback": False, "response": _response_body(resp),
        }
    except Exception as exc:
        # Fast-path unavailable — fall back to the legacy create-and-run path for
        # the first child so the handoff still lands (AIFactory then plans).
        from plan.emit.aifactory_handoff import build_requirements, trigger_api

        if not epic.children:
            return {
                "ok": False, "dry_run": False,
                "errors": [f"from-plan failed ({exc}); no children for fallback"],
            }
        payload = build_requirements(epic.children[0], plan=plan)
        fb = trigger_api(payload, base_url=base_url, project_id=project_id, http=http, dry_run=False)
        return {
            "ok": True, "dry_run": False, "signed": signed, "endpoint": url,
            "fallback": True, "fallback_reason": str(exc), "fallback_response": fb,
        }
