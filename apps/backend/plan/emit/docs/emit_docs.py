"""Docs-emit orchestrator: render once, publish to the selected targets.

P1 wires the always-available repo/directory target. Backstage/Confluence +
per-plan/Settings selection land later (design §6d/§6e). The whole stage is
gated behind ``PFACTORY_DOCS_EMIT`` (default off) and is best-effort — it never
raises, so it cannot break ``PlanService.emit``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .render import render_plan_docs
from .targets.base import DocsTarget
from .targets.repo import RepoDocsTarget

if TYPE_CHECKING:
    from plan.service import PlanSession

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Master switch — the docs emit only runs when explicitly turned on."""
    return os.environ.get("PFACTORY_DOCS_EMIT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _default_root() -> Path:
    """Directory the repo target writes into.

    ``PFACTORY_DOCS_DIR`` wins; else ``~/.pfactory/plan-docs`` (alongside the
    persisted plan sessions, so it survives restarts on the PVC).
    """
    override = os.environ.get("PFACTORY_DOCS_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".pfactory" / "plan-docs"


def _resolve_targets(root: Path | None, updated_at: str) -> list[DocsTarget]:
    """The effective target set. P1: repo only (always included)."""
    return [RepoDocsTarget(root or _default_root(), updated_at=updated_at)]


def emit_docs(
    session: PlanSession,
    *,
    root: Path | None = None,
    targets: list[DocsTarget] | None = None,
) -> list[dict[str, Any]]:
    """Render the plan and publish to each available target.

    Returns a list of per-target result dicts. Never raises.
    """
    updated_at = datetime.now(timezone.utc).isoformat()
    try:
        bundle = render_plan_docs(session)
    except Exception as exc:  # noqa: BLE001 — a render bug must not break emit
        logger.warning("plan docs render failed for %s: %s", session.session_id, exc)
        return [{"target": "render", "status": "error", "detail": {"error": str(exc)}}]

    effective = targets if targets is not None else _resolve_targets(root, updated_at)
    results: list[dict[str, Any]] = []
    for target in effective:
        try:
            if not target.available():
                results.append(
                    {"target": target.name, "status": "skipped", "detail": {}}
                )
                continue
            results.append(target.publish(bundle).as_dict())
        except Exception as exc:  # noqa: BLE001 — isolate target failures
            logger.warning(
                "docs target %s failed: %s", getattr(target, "name", "?"), exc
            )
            results.append(
                {
                    "target": getattr(target, "name", "?"),
                    "status": "error",
                    "detail": {"error": str(exc)},
                }
            )
    return results
