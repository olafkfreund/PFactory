"""RFC-0007 (#84): attach the discovered ``access`` block to the task contract.

Bridges the pure classifier (:mod:`plan.access_discovery`) to the emitted
contract. Config-duck-typed (like ``auth_tagging``): any object exposing a
``targets`` list of ``.pfactory.yml`` target specs works, so this is testable
without constructing a full ``PFactoryConfig``. No-op when there is no config or
no targets — the contract then omits the block entirely (meaning: the task needs
no external/authenticated resource).
"""

from __future__ import annotations

import logging
from typing import Any

from plan.access_discovery import curate_access, discover_access, validate_access

logger = logging.getLogger(__name__)


def _targets_as_dicts(config: Any) -> list[dict]:
    """Normalize a config's targets to plain dicts for the pure classifier.

    Accepts a ``PFactoryConfig``-like object (``.targets``) or a plain dict (the
    snapshotted ``context/pfactory_yml.json``, ``{"targets": [...]}``).
    """
    if isinstance(config, dict):
        targets = config.get("targets") or []
    else:
        targets = getattr(config, "targets", None) or []
    out: list[dict] = []
    for t in targets:
        if hasattr(t, "model_dump"):
            out.append(t.model_dump(exclude_none=True))
        elif isinstance(t, dict):
            out.append(t)
    return out


def attach_access(
    contract: dict,
    config: Any | None,
    spec_text: str = "",
    *,
    approvals: dict | None = None,
) -> dict:
    """Set ``contract['access']`` from ``.pfactory.yml`` targets (RFC-0007).

    No-op (block omitted) when ``config`` is None or has no targets. When
    ``approvals`` (``{resource: approval}``, from ``PlanService.access_approvals``)
    is given, the recorded human-verified curation is applied so ``curated: true``
    + ``human_approval`` land on the contract. Liveness is NOT re-probed here — the
    decision was made and the credential verified at approval time (#86 PR-d), and
    the planner's env differs from TFactory's runtime, so re-checking would wrongly
    un-curate. Mutates and returns ``contract``.
    """
    if config is None:
        return contract
    block = discover_access(_targets_as_dicts(config), spec_text or "")
    if block is not None:
        if approvals:
            # Trust the recorded approval/liveness; apply it deterministically.
            result = curate_access(
                block["requirements"],
                approvals=approvals,
                liveness_check=lambda _r: True,
            )
            block["requirements"] = result["requirements"]
        contract["access"] = block
        # Surface structural (env-independent) readiness gaps at plan time. Env
        # presence is NOT checked here — credentials are injected in TFactory's
        # runtime, not the planner's, so probing env now would false-positive;
        # the full check (incl. missing_credential) is the curation gate's (#86).
        verdict = validate_access(block["requirements"], env_present=lambda _name: True)
        if not verdict["ready"]:
            gaps = ", ".join(f"{i['resource']}:{i['kind']}" for i in verdict["issues"])
            logger.warning("[access] task declares access not testable as-planned: %s", gaps)
    return contract
