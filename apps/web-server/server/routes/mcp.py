"""MCP status endpoint — surfaces the catalog × project × credentials
matrix to the frontend's Project Settings → MCP Servers tab.

Read-only in V1: the response describes WHAT would happen on the next
agent spawn for this project, but doesn't let the caller mutate it.
Force-enable / force-disable still goes through ``.pfactory/.env`` —
deliberately keeping the UI explanatory (not a "MCP control plane by
accident") so operators retain a single source of truth for which
servers run.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

# Add the backend dir to sys.path so the catalog + credential modules
# resolve. Mirrors the pattern used by auto_fix.py et al.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["MCP"])


def _load_projects() -> dict[str, dict]:
    """Read the same projects.json the rest of the routes use.

    Importing the loader from projects.py risks a circular import (this
    module is mounted by main.py alongside projects.py), so we re-resolve
    the path the same way the projects router does.
    """
    from ..config import get_settings

    settings = get_settings()
    projects_file = Path(settings.PROJECTS_DATA_DIR) / "projects.json"
    if not projects_file.exists():
        return {}
    return json.loads(projects_file.read_text())


def _describe_creds_status(provider: str) -> dict[str, Any]:
    """Run a credentials probe and return a UI-friendly summary."""
    try:
        from core.mcp_credentials import get_credential_status

        status = get_credential_status(provider)
        return {
            "available": status.available,
            "source": status.source,
        }
    except ImportError:
        logger.exception("Credential probe unavailable for provider %s", provider)
        return {"available": False, "source": "framework-unavailable"}


def _describe_marker_status(
    marker_keys: list[str], infra_markers: dict[str, bool]
) -> dict[str, Any]:
    """Translate a catalog entry's marker requirement into UI rows."""
    if not marker_keys:
        return {"matches": True, "reason": "always-on", "required": [], "matched": []}
    matched = [k for k in marker_keys if infra_markers.get(k)]
    return {
        "matches": bool(matched),
        "reason": (
            f"matched: {', '.join(matched)}"
            if matched
            else f"none of: {', '.join(marker_keys)}"
        ),
        "required": marker_keys,
        "matched": matched,
    }


@router.get("/{project_id}/mcp-status")
async def get_mcp_status(project_id: str) -> dict[str, Any]:
    """Return the catalog × this project × credentials matrix.

    Frontend uses this to render the Project Settings → MCP Servers
    tab — each row shows what would auto-enable for this project, why,
    and (if a catalog entry is dormant) which prerequisite is missing.

    Response shape::

        {
          "project": {"id": "...", "path": "/path/to/project"},
          "servers": [
            {
              "id": "github",
              "would_enable": true,
              "credentials": {"available": true, "source": "env:GITHUB_TOKEN"},
              "markers": {"matches": true, "reason": "always-on", "required": [], "matched": []},
              "default_for_agents": ["coder", "qa_reviewer", ...],
              "docs_url": "https://..."
            },
            ...
          ]
        }
    """
    projects = _load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    project_path = Path(projects[project_id]["path"]).expanduser()

    try:
        from agents.tools_pkg.mcp_catalog import CATALOG
        from prompts_pkg.project_context import detect_infra_markers
    except ImportError as exc:
        logger.exception("MCP catalog/markers framework unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP framework unavailable",
        ) from exc

    if project_path.exists():
        infra_markers = detect_infra_markers(project_path)
    else:
        # Project directory was deleted out from under us — still useful
        # for the UI to render the catalog with markers={} so the user
        # sees the rows + the cred status, just without marker matches.
        infra_markers = {}

    servers: list[dict[str, Any]] = []
    for entry in CATALOG:
        creds = (
            _describe_creds_status(entry.credential_provider)
            if entry.credential_provider
            else {"available": True, "source": "n/a"}
        )
        markers = _describe_marker_status(entry.marker_capability_keys, infra_markers)
        would_enable = creds["available"] and markers["matches"]
        servers.append(
            {
                "id": entry.id,
                "would_enable": would_enable,
                "credentials": creds,
                "markers": markers,
                "default_for_agents": list(entry.default_for_agents),
                "docs_url": entry.docs_url,
            }
        )

    return {
        "project": {"id": project_id, "path": str(project_path)},
        "servers": servers,
    }
