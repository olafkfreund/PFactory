"""Who may use a terminal session (#3740).

A session is bound to its creating user and project. The service principal
(legacy token, auth-disabled dev user) keeps full access; everyone else needs
to own the session and still hold its project. Every check fails closed.
"""

from pathlib import Path
from typing import Any

from server.database import engine
from server.routes.project_authz import authorize_project_for_user, is_service_principal
from server.services import project_paths


async def project_ok(user: dict[str, Any] | None, project_id: str | None) -> bool:
    """Strict project authorization; denial or lookup failure is False."""
    try:
        async with engine.async_session_factory() as db:
            await authorize_project_for_user(user, project_id, db)
    except Exception:  # noqa: BLE001 - denial or lookup failure fails closed
        return False
    return True


async def may_use(user: dict[str, Any] | None, session: Any) -> bool:
    if is_service_principal(user):
        return True
    if not isinstance(user, dict) or not user.get("id"):
        return False
    return user["id"] == session.owner_id and await project_ok(user, session.project_id)


def project_id_for_path(path: str | None) -> str | None:
    """Registered project id for a project id or a path inside a project."""
    if not path:
        return None
    projects = project_paths.load_projects()
    if path in projects:
        return path
    try:
        target = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    best: tuple[int, str] | None = None
    for project_id, project in projects.items():
        try:
            root = Path(project["path"]).resolve()
        except (KeyError, TypeError, OSError, RuntimeError, ValueError):
            continue
        if (target == root or root in target.parents) and (
            best is None or len(root.parts) > best[0]
        ):
            best = (len(root.parts), project_id)
    return best[1] if best else None


def path_in_project(path: str, project_id: str) -> bool:
    project = project_paths.load_projects().get(project_id)
    try:
        root = Path(project["path"]).resolve()  # type: ignore[index]
        target = Path(path).resolve()
    except (KeyError, TypeError, OSError, RuntimeError, ValueError):
        return False
    return target == root or root in target.parents
