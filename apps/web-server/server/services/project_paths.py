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

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath
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
    with locked_projects_file(projects_file):
        write_project_registry(projects_file, projects)


@contextmanager
def locked_projects_file(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    # flock is per-node; the chart keeps this registry on one RWO volume.
    with path.with_suffix(".json.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_project_registry(path: Path, projects: dict[str, dict[str, Any]]) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(projects, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)  # noqa: PTH105 - explicit atomic replacement
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _roots(project: object) -> list[tuple[PurePath, Path]]:
    if not isinstance(project, dict) or not project.get("path"):
        return []
    raw = str(project["path"])
    try:
        return [(PurePath(os.path.normpath(raw)), Path(raw).resolve())]
    except (OSError, RuntimeError, ValueError):
        return []


def project_id_for_path(path: str | None) -> str | None:
    """Find the longest registered project root containing a resolved path."""
    if not path:
        return None
    projects = load_projects()
    if path in projects:
        return path
    if not PurePath(path).is_absolute():
        return None
    lexical = PurePath(os.path.normpath(path))
    # Resolve only paths under registered roots; containment also checks symlinks.
    candidates = [
        (project_id, root)
        for project_id, project in projects.items()
        for lexical_root, root in _roots(project)
        if lexical in (lexical_root, root)
        or lexical_root in lexical.parents
        or root in lexical.parents
    ]
    if not candidates:
        return None
    try:
        from server.services.git_utils import (  # noqa: PLC0415 - git_utils imports this module
            _confine,
        )

        target = _confine(path, [root for _, root in candidates], "path")
    except (OSError, RuntimeError, ValueError):
        return None
    matches = [
        (len(root.parts), project_id)
        for project_id, root in candidates
        if target == root or root in target.parents
    ]
    return max(matches)[1] if matches else None


def path_in_project(path: str, project_id: str) -> bool:
    return PurePath(path).is_absolute() and project_id_for_path(path) == project_id


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
