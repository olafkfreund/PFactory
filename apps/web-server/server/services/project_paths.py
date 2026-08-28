"""The projects.json registry and the project-id -> filesystem-path resolver.

Lives in ``services`` rather than in ``routes.projects`` because every route
module needs the resolver, but ``routes.projects`` mounts those same modules as
sub-routers (``from . import changelog, context, git, github, insights`` at
module level). That left the resolver reachable only through a function-local
``from .projects import ...`` in each caller -- an import cycle CodeQL flags at
every one of those sites. Nothing here imports ``routes``, so callers can
import it at module level and the cycle does not exist.

``load_projects``/``save_projects`` moved with the resolver rather than being
imported back from ``routes.projects``: importing them back is precisely the
edge that would recreate the cycle. ``routes.projects`` re-exports all of these
names, so callers that still reach for them there keep working.
"""

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from server.config import get_settings


def get_projects_file() -> Path:
    """Get path to the projects data file."""
    settings = get_settings()
    return Path(settings.PROJECTS_DATA_DIR) / "projects.json"


def load_projects() -> dict[str, dict[str, Any]]:
    """Load projects from disk."""
    projects_file = get_projects_file()
    if projects_file.exists():
        loaded: dict[str, dict[str, Any]] = json.loads(projects_file.read_text())
        return loaded
    return {}


def save_projects(projects: dict[str, dict[str, Any]]) -> None:
    """Save projects to disk."""
    projects_file = get_projects_file()
    projects_file.parent.mkdir(parents=True, exist_ok=True)
    projects_file.write_text(json.dumps(projects, indent=2))


#: Detail returned when a repo-only project is asked for a filesystem path.
#: Module-level so callers and tests can match it without copying the wording.
NO_LOCAL_CLONE_DETAIL = (
    "Project {project_id} has no local clone on this server (it was registered "
    "from a repo only), so there is no directory to read or write."
)


def resolve_project_path(project_id: str) -> Path:
    """Resolve a project id to a usable filesystem path, or raise.

    Single source of truth for the ``load_projects() -> 404 -> Path(...)`` idiom
    that several route modules each re-implemented. The 404 detail is kept
    identical to those copies so callers behave exactly as before.

    An empty ``path`` is refused with 409 rather than converted (#647). It is a
    sentinel, not a location: ``ensure_tracked_project`` writes ``""`` to mean
    "repo-only; no local clone yet", and both ``analyze_project`` and
    :func:`~server.services.git_utils.registered_project_roots` already read it
    that way. But ``Path("")`` is ``Path(".")``, so converting it hands the
    caller a relative path resolved against the server's CWD -- on the read-only
    container root that surfaces as ``OSError: [Errno 30]`` naming only
    ``.pfactory``, and on a writable one it would quietly create the project's
    ``.pfactory`` in whatever directory the server happens to have started in.
    Absent and "the current directory" are the two meanings riding on the same
    value; separating them has to happen at the one conversion, or every caller
    inherits a plausible wrong directory.
    """
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    path = projects[project_id].get("path") or ""
    if not path:
        raise HTTPException(
            status_code=409, detail=NO_LOCAL_CLONE_DETAIL.format(project_id=project_id)
        )
    return Path(path)


def resolve_project_path_or_error(
    project_id: str,
) -> tuple[Path | None, dict[str, object] | None]:
    """:func:`resolve_project_path`, for routes that answer 200 with an envelope.

    Several route modules report failure as ``{"success": False, "error": ...}``
    at status 200 -- the portal renders that ``error`` string verbatim -- so they
    cannot let the resolver's ``HTTPException`` escape without changing their
    status codes. Translate it here once instead of in each module (#655).
    """
    try:
        return resolve_project_path(project_id), None
    except HTTPException as exc:
        return None, {"success": False, "error": str(exc.detail)}
