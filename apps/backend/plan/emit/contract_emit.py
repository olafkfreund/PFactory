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

import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from plan.emit.access_block import attach_access
from plan.emit.constitution import attach_constitution
from plan.emit.contract_builder import build_task_contract
from plan.emit.cost_router import apply_cost_routing
from plan.emit.environment_block import attach_environment
from plan.emit.execution_profile import attach_execution
from plan.emit.handoff_sanitize import attach_constraints
from plan.emit.house_standards import attach_house_standards
from plan.emit.migration_block import attach_migration
from plan.emit.review_tier import attach_review_tier
from plan.emit.signing import attach_signature, key_from_env
from plan.emit.task_contract import validate_contract
from plan.emit.tfactory_block import attach_tfactory
from plan.emit.tier_profile import apply_tier
from plan.emit.verification import attach_verification

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.emit.aifactory_handoff import HttpClient
    from plan.models import NormalizedPlan
    from plan.review.models import PlanReview


logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    config: Any | None = None,
    spec_text: str = "",
    approvals: dict | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Compose the complete (unsigned) Task Contract from all #65 blocks.

    When ``config`` (a parsed ``.pfactory.yml``) is provided, an RFC-0007
    ``access`` block is discovered from its targets and attached; ``approvals``
    (from ``PlanService.access_approvals``) applies recorded human-verified
    curation so ``curated: true`` lands on the contract (#86). Both optional, so
    existing callers are unaffected.
    """
    contract = build_task_contract(plan, epic, repo=repo, correlation_key=correlation_key)
    # Multi-tenancy (#308): carry the tenant as OPTIONAL additive provenance
    # metadata (the hub schema's provenance allows additional properties), so
    # AIFactory can keep the PARR chain tenant-scoped. Omitted for the default
    # tenant, keeping every existing single-tenant contract byte-identical.
    if tenant_id and tenant_id != "default":
        contract.setdefault("provenance", {})["tenant_id"] = tenant_id
    attach_execution(contract, plan, epic)
    if review is not None:
        attach_review_tier(contract, review)
    attach_verification(contract, plan, epic)
    attach_tfactory(contract, plan, epic)
    # RFC-0011: the label-driven difficulty tier (low|medium|hard) wins over the
    # per-complexity defaults above — runs AFTER execution/review_tier/tfactory so
    # the tier has the last word. No-op when no tier was classified. A blocking
    # gate finding still forces blocking (apply_tier only ratchets review_tier up).
    apply_tier(contract, getattr(plan, "autonomy_tier", None))
    # RFC-0014: cost-aware, capability-aware routing. Runs AFTER apply_tier so the
    # tier floor is set, then picks the cheapest capable per-role model under the
    # scored cost ceiling (strong for planning/governed, cheaper for code/test)
    # and writes execution.phase_models + execution.routing. Additive + never
    # raises — leaves the tier/complexity execution.model in place as the fallback.
    apply_cost_routing(contract)
    # RFC-0005: derive the environment manifest from the tfactory lanes (must run
    # AFTER attach_tfactory). Declares the per-task toolchain (nix provisioning)
    # so build (AIFactory) and verify (TFactory) cannot drift.
    attach_environment(contract)
    # RFC-0010: for a migration, record both languages + the equivalence lane the
    # differential verifier consumes (must run after environment + tfactory).
    attach_migration(contract, plan)
    # Carry sanitized live-cloud enrichment as epic_context constraints (#80).
    attach_constraints(contract, plan)
    # RFC-0012: surface the team's house standards (RFC-0010 baseline conventions
    # + best-effort Backstage lookup) so the fleet can FOLLOW them and the
    # standards_conformance gate can prove they were applied. Best-effort, never
    # raises; baseline-only when Backstage is unavailable.
    attach_house_standards(contract, plan)
    # RFC-0015 §3.1: surface the per-project constitution (governing principles
    # from .factory/constitution.md, captured during recon) so the fleet honours
    # it and the standards_conformance gate enforces enforceable=true clauses as
    # HARD checks. Additive to house_standards; best-effort, never raises;
    # available=false when the repo carries no constitution.
    attach_constitution(contract, plan)
    # RFC-0007: access requirements discovered from .pfactory.yml (#84) + recorded
    # human-verified curation applied (#86).
    attach_access(contract, config, spec_text, approvals=approvals)
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
        "spec_id": spec_id,
        "task_id": None,
        "project_id": project_id,
    }
    base_result = {
        "ok": True,
        "dry_run": False,
        "signed": signed,
        "endpoint": url,
        "fallback": False,
    }

    get = getattr(http, "get", None)
    readback_on = callable(get) and _handshake_enabled()

    last_mismatches: list[str] = []
    attempts = max(1, max_retries + 1)
    for attempt in range(attempts):
        # AIFactory's /api/tasks/from-plan takes project_id/title/description as
        # query params and the signed contract under a ``plan`` body field
        # (FromPlanRequest). Posting the bare contract 422s ("body.plan
        # required") → silent create-and-run fallback. (#517 PARR)
        _title = str(contract.get("feature") or spec_id or "task")[:120]
        _desc = "; ".join(contract.get("final_acceptance") or [])[:500] or _title
        resp = http.post(
            url,
            params={"project_id": project_id, "title": _title, "description": _desc},
            json={"plan": contract},
        )
        body = _response_body(resp)
        task_id = _extract_task_id(body)
        correlation["task_id"] = task_id

        if not readback_on:
            # Degrade gracefully: the create is confirmed, just not verified.
            return {
                **base_result,
                "response": body,
                "correlation": dict(correlation),
                "verified": False,
                "warnings": ["read-back unavailable"],
            }

        if task_id is None:
            last_mismatches = ["create response carried no task id to read back"]
            continue

        task = _response_body(get(_task_url(base_url, task_id)))
        mismatches = _verify_readback(contract, task)
        if not mismatches:
            return {
                **base_result,
                "response": body,
                "correlation": dict(correlation),
                "verified": True,
                "attempts": attempt + 1,
            }
        last_mismatches = mismatches

    # Exhausted retries with the create succeeding but read-back never matching.
    return {
        **base_result,
        "ok": False,
        "verified": False,
        "correlation": dict(correlation),
        "mismatches": last_mismatches,
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
    config: Any | None = None,
    spec_text: str = "",
    approvals: dict | None = None,
    max_retries: int = 2,
    dry_run: bool = True,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Assemble, validate, sign, and (unless dry-run) emit the contract.

    ``config`` (a parsed ``.pfactory.yml``) + ``spec_text`` enable the RFC-0007
    ``access`` block (#84); ``approvals`` applies recorded human-verified curation
    (#86). All optional and no-op when absent.

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
        plan,
        epic,
        review,
        repo=repo,
        correlation_key=correlation_key,
        config=config,
        spec_text=spec_text,
        approvals=approvals,
        tenant_id=tenant_id,
    )
    errors = validate_contract(contract)
    if errors:
        return {"ok": False, "dry_run": dry_run, "errors": errors, "contract": contract}

    # #401: key and kid resolve together. An explicit `key=` override signs the
    # legacy way (no kid) — only the environment knows which kid a key belongs to.
    signing_key, signing_kid = (key, None) if key else key_from_env("pfactory")
    signed = False
    if signing_key:
        attach_signature(
            contract,
            key=signing_key,
            approval_timestamp=approval_timestamp or _utcnow_iso(),
            kid=signing_kid,
        )
        signed = True

    url = _from_plan_url(base_url)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "signed": signed,
            "endpoint": url,
            "contract": contract,
        }

    if http is None:
        raise ValueError("live emit_contract requires an injected `http` client")

    spec_id = spec_id or getattr(plan, "plan_id", None)

    try:
        return _post_with_readback(
            contract,
            http=http,
            url=url,
            base_url=base_url,
            project_id=project_id,
            spec_id=spec_id,
            signed=signed,
            max_retries=max_retries,
        )
    except Exception as exc:
        # Fast-path unavailable — fall back to the legacy create-and-run path for
        # the first child so the handoff still lands (AIFactory then plans).
        # Factory#718: `exc` is whatever `_post_with_readback`'s transport call
        # raised -- urllib, TLS, or AIFactory's own response parsing -- and its
        # text was never reviewed for what it reveals (a host, a port, an
        # internal URL). Logged in full server-side; only the class name and a
        # static sentence cross into the response body below.
        logger.warning(
            "emit_contract fast-path failed for spec_id=%s, falling back to create-and-run: %s",
            spec_id,
            type(exc).__name__,
            exc_info=exc,
        )
        from plan.emit.aifactory_handoff import build_requirements, trigger_api

        if not epic.children:
            return {
                "ok": False,
                "dry_run": False,
                "errors": ["from-plan failed; no children for fallback"],
            }
        # The fallback ALSO POSTs to AIFactory (create-and-run) and can fail the
        # same way. It must not escape as an uncaught 500 (#321): a transport error
        # here — e.g. an AssertionError bubbling out of urllib on a redirect/odd
        # response — surfaces as a clean error body, not a stack trace.
        try:
            payload = build_requirements(epic.children[0], plan=plan)
            fb = trigger_api(
                payload,
                base_url=base_url,
                project_id=project_id,
                http=http,
                dry_run=False,
            )
        except Exception as fb_exc:  # noqa: BLE001 - the handoff must never 500
            logger.warning(
                "emit_contract create-and-run fallback also failed for spec_id=%s: %s",
                spec_id,
                type(fb_exc).__name__,
                exc_info=fb_exc,
            )
            return {
                "ok": False,
                "dry_run": False,
                "endpoint": url,
                "errors": ["from-plan failed; create-and-run fallback also failed"],
            }
        return {
            "ok": True,
            "dry_run": False,
            "signed": signed,
            "endpoint": url,
            "fallback": True,
            "fallback_reason": "from-plan failed; used create-and-run fallback",
            "fallback_response": fb,
        }
