"""Who may use a terminal session (#3740).

A session is bound to its creating user and project. The service principal
(legacy token, auth-disabled dev user) keeps full access; everyone else needs
to own the session and still hold its project. Every check fails closed.
"""

from typing import Any

from server.config import get_settings
from server.database import engine
from server.routes.project_authz import authorize_project_for_user, is_service_principal
from server.services import project_paths


async def project_ok(
    user: dict[str, Any] | None, project_id: str | None, role: str = "viewer"
) -> bool:
    """Strict project authorization; denial or lookup failure is False."""
    try:
        async with engine.async_session_factory() as db:
            await authorize_project_for_user(user, project_id, db, role)
    except Exception:  # noqa: BLE001 - denial or lookup failure fails closed
        return False
    return True


async def may_use(user: dict[str, Any] | None, session: Any) -> bool:
    if get_settings().DISABLE_AUTH and (user or {}).get("id") == "default":
        return True
    if session.owner_id is None:
        return bool(session.project_id) and await project_ok(user, session.project_id, "admin")
    if is_service_principal(user):
        return True
    if not isinstance(user, dict) or not user.get("id"):
        return False
    return user["id"] == session.owner_id and await project_ok(user, session.project_id, "admin")


project_id_for_path = project_paths.project_id_for_path
path_in_project = project_paths.path_in_project
