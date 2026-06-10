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
from .targets.backstage import BackstageTarget
from .targets.base import DocsTarget
from .targets.confluence import ConfluenceTarget
from .targets.repo import RepoDocsTarget

if TYPE_CHECKING:
    from plan.service import PlanSession

logger = logging.getLogger(__name__)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    """Master switch — the docs emit only runs when explicitly turned on."""
    return _truthy("PFACTORY_DOCS_EMIT")


def _default_root() -> Path:
    """Directory the repo target writes into.

    ``PFACTORY_DOCS_DIR`` wins; else ``~/.pfactory/plan-docs`` (alongside the
    persisted plan sessions, so it survives restarts on the PVC).
    """
    override = os.environ.get("PFACTORY_DOCS_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".pfactory" / "plan-docs"


def _resolve_targets(
    root: Path | None, updated_at: str, *, repo: str | None
) -> list[DocsTarget]:
    """The effective target set: repo always; Backstage/Confluence when configured.

    Per-plan/Settings selection (design §6d/§6e) lands in P4; for now a target is
    added when its env config is present and its ``available()`` returns True.
    The repo/GitHub doc is the default and is always included.
    """
    out: list[DocsTarget] = [
        RepoDocsTarget(root or _default_root(), updated_at=updated_at)
    ]
    git_write = _truthy("PFACTORY_DOCS_GIT_WRITE")
    if _truthy("PFACTORY_DOCS_BACKSTAGE") or os.environ.get("BACKSTAGE_BASE_URL"):
        out.append(BackstageTarget(repo=repo, git_write=git_write))
    if _truthy("PFACTORY_DOCS_CONFLUENCE") or os.environ.get("CONFLUENCE_BASE_URL"):
        out.append(ConfluenceTarget())
    return out


def connections_to_targets(
    connections: list[dict[str, Any]],
    *,
    repo: str | None = None,
    git_write: bool = False,
    selected: list[str] | None = None,
) -> list[DocsTarget]:
    """Build remote targets from Settings connection dicts (design §6d/§6e seam).

    The web-server passes user/org ``DocsTargetConnection`` rows here so target
    selection is Settings-driven (and per-plan, via ``selected``) instead of env.
    The repo/GitHub default is added by the orchestrator, not here.

    Each connection: ``{kind, base_url, api_token, space?, enabled_by_default?}``.
    A connection is used when ``selected`` lists its kind, else when
    ``enabled_by_default`` is set.
    """
    out: list[DocsTarget] = []
    for conn in connections:
        kind = (conn.get("kind") or "").lower()
        use = kind in selected if selected is not None else bool(conn.get("enabled_by_default"))
        if not use:
            continue
        if kind == "backstage":
            out.append(BackstageTarget(
                base_url=conn.get("base_url"), repo=repo, git_write=git_write,
            ))
        elif kind == "confluence":
            out.append(ConfluenceTarget(
                base_url=conn.get("base_url"), token=conn.get("api_token"),
                space=conn.get("space"),
            ))
    return out


def emit_docs(
    session: PlanSession,
    *,
    repo: str | None = None,
    root: Path | None = None,
    targets: list[DocsTarget] | None = None,
    connections: list[dict[str, Any]] | None = None,
    selected: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Render the plan and publish to each available target.

    Target resolution precedence:

    * ``targets`` given → exactly those (test/explicit injection).
    * ``connections`` given → the repo/GitHub default **plus** the Settings
      connections selected for this plan (design §6d/§6e). This is the path the
      web-server uses: it passes the user's ``DocsTargetConnection`` rows.
    * neither → env-resolved set (repo always + Backstage/Confluence when
      env-configured).

    Returns a list of per-target result dicts. Never raises.
    """
    updated_at = datetime.now(timezone.utc).isoformat()
    try:
        bundle = render_plan_docs(session)
    except Exception as exc:  # noqa: BLE001 — a render bug must not break emit
        logger.warning("plan docs render failed for %s: %s", session.session_id, exc)
        return [{"target": "render", "status": "error", "detail": {"error": str(exc)}}]

    if targets is not None:
        effective = targets
    elif connections is not None:
        effective = [RepoDocsTarget(root or _default_root(), updated_at=updated_at)]
        effective += connections_to_targets(
            connections,
            repo=repo,
            git_write=_truthy("PFACTORY_DOCS_GIT_WRITE"),
            selected=selected,
        )
    else:
        effective = _resolve_targets(root, updated_at, repo=repo)
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
