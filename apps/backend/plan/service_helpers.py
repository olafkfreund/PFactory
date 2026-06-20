"""Pure, stateless helpers extracted from :mod:`plan.service` (#194).

A behaviour-preserving decomposition of the large ``PlanService`` module: these
are the self-contained functions that depend only on their arguments and the
environment — no session/store state, no monkeypatched module-level seams. They
are re-exported from :mod:`plan.service` so every historical import path
(``from plan.service import _knowledge_connector_kwargs`` etc.) keeps working.

Kept here:
  * environment plumbing — knowledge-connector kwargs, persistence/store-dir
    resolution, workspace access-input loading;
  * the RFC-0011 difficulty-tier routing (``_carry_tier`` / ``_route_tier``);
  * the additive RFC-0013 deployment + #E template review-finding seams;
  * the board-column projection.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from plan.feasibility.deployment import (
    assess_deployment_readiness,
    deployment_findings,
    inject_deployment_acs,
)

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.review.models import PlanReview

logger = logging.getLogger("plan.service")

# Task-board column ids (shared with the frontend kanban — see constants/task.ts).
# "backlog" is rendered as "Plans ready".
BoardColumn = str  # backlog | in_progress | ai_review | human_review | done

# RFC-0011 tier precedence (highest wins). hard > medium > low.
_TIER_RANK = {"low": 0, "medium": 1, "hard": 2}


def knowledge_connector_kwargs(name: str, wiki_root: str | None) -> dict[str, object]:
    """Build a knowledge connector's constructor kwargs from the environment.

    The connectors accept ``base_url`` / ``token`` (etc.) but read nothing
    themselves, so without this the enrichment loop ran them unconfigured and
    they returned nothing. Each source has its own ``PFACTORY_<SOURCE>_*`` vars;
    empty/unset values are dropped so an unconfigured connector simply reports
    ``available() is False`` and degrades to an empty result.
    """
    env = os.environ.get

    def _kw(**pairs: str | None) -> dict[str, object]:
        return {k: v for k, v in pairs.items() if v}

    if name == "backstage":
        return _kw(
            base_url=env("PFACTORY_BACKSTAGE_URL"),
            token=env("PFACTORY_BACKSTAGE_TOKEN"),
        )
    if name == "confluence":
        return _kw(
            base_url=env("PFACTORY_CONFLUENCE_URL"),
            token=env("PFACTORY_CONFLUENCE_TOKEN"),
            email=env("PFACTORY_CONFLUENCE_EMAIL"),
        )
    if name == "gitbook":
        return _kw(
            token=env("PFACTORY_GITBOOK_TOKEN"),
            space_id=env("PFACTORY_GITBOOK_SPACE_ID"),
        )
    if name == "notion":
        return _kw(token=env("PFACTORY_NOTION_TOKEN"))
    if name == "git-markdown":
        return _kw(root=wiki_root)
    return {}


# Lifecycle status → kanban column. ``processed`` and ``rejected`` both land in
# human_review: AI review is complete and a person must approve a clean plan or
# fix a blocked/rejected one. Any status not listed defaults to backlog.
_BOARD_COLUMN: dict[str, BoardColumn] = {
    "approved": "done",
    "emitted": "done",
    "rejected": "human_review",  # needs attention / edit
    "ingested": "backlog",
    "processing": "in_progress",
    "reviewing": "ai_review",
    "processed": "human_review",
}


def board_state(status: str, review: PlanReview | None) -> BoardColumn:  # noqa: ARG001
    """Project a session's (status, review) onto a kanban column (#5).

    plans-ready (backlog) → in-progress (processing) → AI review (gates running)
    → human review (AI done: awaiting sign-off, blocked, or needs edit) →
    done (approved by AI *and* human, or already emitted).

    ``review`` is part of the historical signature and currently informational
    only — the column is derived from ``status`` alone.
    """
    return _BOARD_COLUMN.get(status, "backlog")


def carry_tier(plan: NormalizedPlan, tier: str | None) -> NormalizedPlan:
    """Stamp a normalized RFC-0011 tier onto the plan (no-op for unknown)."""
    # Deferred import (kept from the original service.py): the emit subtree is
    # heavy and only needed when a tier is actually being routed.
    from plan.emit.tier_profile import normalize_tier  # noqa: PLC0415

    canonical = normalize_tier(tier)
    if canonical is None:
        return plan
    return plan.model_copy(update={"autonomy_tier": canonical})


def route_tier(current: str | None, *, is_migration: bool) -> str | None:
    """Resolve the final tier for a plan (RFC-0011, #182).

    ``change_mode == migration`` (a rewrite) forces ``hard``; otherwise the
    highest of the carried tier wins. Returns ``None`` only when no tier was
    carried and it is not a migration (back-compat: emit derives from complexity).
    """
    from plan.emit.tier_profile import normalize_tier  # noqa: PLC0415

    carried = normalize_tier(current)
    forced = "hard" if is_migration else None
    candidates = [t for t in (carried, forced) if t is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda t: _TIER_RANK[t])


def attach_deployment(plan: NormalizedPlan, epic: EpicPlan) -> list:
    """Derive + attach the RFC-0013 deployment block; return its review findings.

    Stamps ``epic.deployment`` and injects the deployment ACs when a deployment
    dimension exists. Additive + safe: returns ``[]`` (and leaves the epic
    untouched) when there is no deployment surface or analysis fails — deployment
    analysis must never break a plan run.
    """
    try:
        block = assess_deployment_readiness(plan, epic)
    except Exception:  # noqa: BLE001 — deployment analysis must never break a run
        return []
    if block is None:
        return []
    epic.deployment = block
    inject_deployment_acs(epic, block)
    return deployment_findings(block)


def template_findings(session: object, plan: NormalizedPlan, descriptor: object) -> list:
    """Template-policy findings (#E). OPT-IN: only a user-selected template gates.

    Records the auto-matched template as a non-gating suggestion, defaults the
    category, and runs the selected template's policy check. Best-effort — docs
    must never break emit, so any failure degrades to no findings.
    """
    # Deferred import (kept from the original service.py) to keep this service
    # module dependency-light: the templates subtree is only needed here.
    from plan.templates import build_context, load_templates, select_template  # noqa: PLC0415

    try:
        suggested = select_template(plan)
        session.suggested_template = suggested.metadata.name if suggested else ""
        if not session.selected_category:
            session.selected_category = getattr(descriptor, "category", "")
        if session.selected_template:
            tmpl = load_templates().get(session.selected_template)
            if tmpl is not None:
                return tmpl.check(build_context(plan))
    except Exception:  # noqa: BLE001 — docs must never break emit
        return []
    return []


def persist_enabled() -> bool:
    """Whether plan sessions are persisted to disk.

    Opt-in via ``PFACTORY_PLAN_PERSIST`` so unit tests (which construct bare
    ``PlanService()`` instances) stay hermetic by default — no disk reads/writes,
    identical to the historical in-memory behaviour. Production deployments set
    it to survive pod restarts (the in-memory store was wiped on every restart).
    """
    return os.environ.get("PFACTORY_PLAN_PERSIST", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def default_store_dir() -> Path:
    """Directory that holds one ``<session_id>.json`` per plan session.

    Defaults under ``~/.pfactory`` — which the deployment mounts on a
    PersistentVolumeClaim, so sessions survive restarts. Override with
    ``PFACTORY_PLAN_STORE_DIR``.
    """
    override = os.environ.get("PFACTORY_PLAN_STORE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".pfactory" / "plan-sessions"


def workspaces_dir() -> Path:
    """Base of the per-spec workspace context snapshots (RFC-0007 / #84).

    ``<base>/workspaces/{project_id}/specs/{spec_id}/context/`` holds the
    snapshotted ``pfactory_yml.json`` + ``aifactory_spec.md`` (see
    ``workspaces.snapshotter``). Override the base with ``PFACTORY_WORKSPACES_DIR``.
    """
    override = os.environ.get("PFACTORY_WORKSPACES_DIR", "").strip()
    base = Path(override) if override else Path.home() / ".pfactory" / "workspaces"
    return base


def load_access_inputs(project_id: str, spec_id: str) -> tuple[dict | None, str]:
    """Best-effort: load the snapshotted .pfactory.yml + spec for access discovery.

    Returns ``(config_dict_or_None, spec_text)``. Never raises: a missing snapshot
    (the common case for plans with no declared targets) yields ``(None, "")`` so
    the contract simply omits the RFC-0007 ``access`` block.
    """
    try:
        ctx = workspaces_dir() / str(project_id) / "specs" / str(spec_id) / "context"
        config: dict | None = None
        pj = ctx / "pfactory_yml.json"
        if pj.is_file():
            loaded = json.loads(pj.read_text(encoding="utf-8"))
            config = loaded if isinstance(loaded, dict) else None
        spec_text = ""
        sm = ctx / "aifactory_spec.md"
        if sm.is_file():
            spec_text = sm.read_text(encoding="utf-8", errors="replace")
        return config, spec_text
    except Exception:  # noqa: BLE001 - access discovery must never break emit
        return None, ""
