"""Who may use a terminal session (#3740).

A session is bound to its creating user and project. The service principal
(legacy token, auth-disabled dev user) keeps full access; everyone else needs
to own the session and still hold its project. Every check fails closed.
"""

import os
from pathlib import Path, PurePath
from typing import Any

from server.database import engine
from server.config import get_settings
from server.routes.project_authz import authorize_project_for_user, is_service_principal
from server.services import project_paths


async def project_ok(user: dict[str, Any] | None, project_id: str | None, role: str = "viewer") -> bool:
    """Strict project authorization; denial or lookup failure is False."""
    try:
        async with engine.async_session_factory() as db:
            await authorize_project_for_user(user, project_id, db, role)
    except Exception:  # noqa: BLE001 - denial or lookup failure fails closed
        return False
    return True


async def may_use(user: dict[str, Any] | None, session: Any) -> bool:
    if session.owner_id is None:
        return bool(session.project_id) and await project_ok(user, session.project_id, "admin")
    if is_service_principal(user) or (get_settings().DISABLE_AUTH and (user or {}).get("id") == "default"):
        return True
    if not isinstance(user, dict) or not user.get("id"):
        return False
    return user["id"] == session.owner_id and await project_ok(user, session.project_id, "admin")


def _roots(project: object) -> list[PurePath]:
    """Normalised and resolved forms of a registered project's path, from trusted data."""
    # A repo-only project stores "" (no clone); Path("") would be the CWD.
    if not isinstance(project, dict) or not project.get("path"):
        return []
    raw = str(project["path"])
    try:
        return [PurePath(os.path.normpath(raw)), Path(raw).resolve()]
    except (OSError, RuntimeError, ValueError):
        return [PurePath(os.path.normpath(raw))]


def _inside(target: PurePath, root: PurePath) -> bool:
    return target == root or root in target.parents


def project_id_for_path(path: str | None) -> str | None:
    """Registered project id for a project id or a path inside a project."""
    if not path:
        return None
    projects = project_paths.load_projects()
    if path in projects:
        return path
    if not PurePath(path).is_absolute():
        return None
    # Pure path arithmetic: the caller's string never reaches a filesystem call.
    target = Path(path).resolve()
    best: tuple[int, str] | None = None
    for project_id, project in projects.items():
        for root in _roots(project):
            if not isinstance(root, Path):
                continue
            if _inside(target, root) and (best is None or len(root.parts) > best[0]):
                best = (len(root.parts), project_id)
    return best[1] if best else None


def path_in_project(path: str, project_id: str) -> bool:
    if not PurePath(path).is_absolute():
        return False
    target = Path(path).resolve()
    return any(
        _inside(target, root) for root in _roots(project_paths.load_projects().get(project_id)) if isinstance(root, Path)
    )
