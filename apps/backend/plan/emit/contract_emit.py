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

import os
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


_HANDSHAKE_ENV = "PFACTORY_AIFACTORY_HANDSHAKE"
_TRUTHY = {"1", "true", "yes", "on"}


def _handshake_enabled() -> bool:
    """True when strict read-back verification is opted in via env (#81)."""
    return os.getenv(_HANDSHAKE_ENV, "").strip().lower() in _TRUTHY


def _from_plan_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/tasks/from-plan"


def _task_url(base_url: str, task_id: str) -> str:
    return f"{base_url.rstrip('/')}/api/tasks/{task_id}"


def _extract_task_id(body: Any) -> str | None:
    """Pull a task id (``taskId``/``task_id``) out of a create response body."""
    if not isinstance(body, dict):
        return None
    for key in ("taskId", "task_id"):
        value = body.get(key)
        if value:
            return str(value)
    return None


def _verify_readback(contract: dict[str, Any], task: Any) -> list[str]:
    """Assert a read-back task echoes the contract's required fields.

    Returns a list of mismatch strings (empty == verified). Checks the created
    task's ``title`` matches ``contract['feature']``, ``metadata.complexity`` is
    present, and — only when the task carries ``requireReviewBeforeCoding`` — that
    it is truthy. Tolerant of camelCase/snake_case + nested ``metadata``.
    """
    mismatches: list[str] = []
    if not isinstance(task, dict):
        return ["read-back returned a non-object task body"]

    expected_title = contract.get("feature")
    actual_title = task.get("title") or task.get("name")
    if expected_title and actual_title != expected_title:
        mismatches.append(f"title: expected {expected_title!r}, got {actual_title!r}")

    metadata = task.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else task
    complexity = metadata.get("complexity")
    if not complexity:
        mismatches.append("metadata.complexity: missing")

    # Only enforce review-gate when the created task reports it at all.
    for key in ("requireReviewBeforeCoding", "require_review_before_coding"):
        if key in metadata:
            if not metadata.get(key):
                mismatches.append(f"{key}: expected truthy, got {metadata.get(key)!r}")
            break

    return mismatches


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


def _post_with_readback(
    contract: dict[str, Any],
    *,
    http: HttpClient,
    url: str,
    base_url: str,
    project_id: str,
    spec_id: str | None,
    signed: bool,
    max_retries: int,
) -> dict[str, Any]:
    """POST the contract and (when enabled) read the created task back.

    Raises so the caller's ``except`` triggers the create-and-run fallback when
    the POST itself fails; a *read-back mismatch* is not an exception (the create
    succeeded) and is returned as ``ok: False`` with ``mismatches``.
    """
    correlation: dict[str, Any] = {
        "spec_id": spec_id, "task_id": None, "project_id": project_id,
    }
    base_result = {
        "ok": True, "dry_run": False, "signed": signed,
        "endpoint": url, "fallback": False,
    }

    get = getattr(http, "get", None)
    readback_on = callable(get) and _handshake_enabled()

    last_mismatches: list[str] = []
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        resp = http.post(url, params={"project_id": project_id}, json=contract)
        body = _response_body(resp)
        task_id = _extract_task_id(body)
        correlation["task_id"] = task_id

        if not readback_on:
            # Degrade gracefully: the create is confirmed, just not verified.
            return {
                **base_result, "response": body, "correlation": dict(correlation),
                "verified": False, "warnings": ["read-back unavailable"],
            }

        if task_id is None:
            last_mismatches = ["create response carried no task id to read back"]
            continue

        task = _response_body(get(_task_url(base_url, task_id)))
        mismatches = _verify_readback(contract, task)
        if not mismatches:
            return {
                **base_result, "response": body, "correlation": dict(correlation),
                "verified": True, "attempts": attempt + 1,
            }
        last_mismatches = mismatches

    # Exhausted retries with the create succeeding but read-back never matching.
    return {
        **base_result, "ok": False, "verified": False,
        "correlation": dict(correlation), "mismatches": last_mismatches,
        "attempts": attempts,
    }


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
    spec_id: str | None = None,
    max_retries: int = 2,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Assemble, validate, sign, and (unless dry-run) emit the contract.

    Returns a result dict: ``{ok, dry_run, signed, endpoint, contract, ...}``.
    On a validation failure ``ok`` is False and ``errors`` lists the problems —
    an incomplete contract is never POSTed. Live emit POSTs to ``/from-plan`` and
    falls back to ``create-and-run`` (first child) if that raises.

    Validated handshake (#81): after a successful POST the ``task_id`` is read
    from the response. When the injected client exposes ``get`` AND
    ``PFACTORY_AIFACTORY_HANDSHAKE`` is truthy, the created task is read back and
    its title / ``metadata.complexity`` / ``requireReviewBeforeCoding`` are
    asserted against the contract; on mismatch the POST is retried up to
    ``max_retries`` times before returning ``ok: False`` with ``mismatches``.
    Without ``get`` or the flag, it degrades to "create-confirmed" with a
    ``warnings: ["read-back unavailable"]``. A ``correlation``
    ``{spec_id, task_id, project_id}`` is always included on a live create.
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

    spec_id = spec_id or getattr(plan, "plan_id", None)

    try:
        return _post_with_readback(
            contract, http=http, url=url, base_url=base_url,
            project_id=project_id, spec_id=spec_id, signed=signed,
            max_retries=max_retries,
        )
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
