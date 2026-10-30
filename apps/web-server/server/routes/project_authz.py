"""Org role checks for project-, task- and plan-scoped routes (Factory#312 S2).

Port of AIFactory ``project_authz.py``. The service principal (legacy
``API_TOKEN`` / auth-disabled dev user, marked ``is_service``) bypasses per-org
checks; everyone else needs an ``OrgMember`` role in the project's org.

Shadow mode (``RBAC_ENFORCE_TASK_ROUTES`` off, the default): the same decision
runs, but a denial is only logged (``rbac.would_deny``) and audited. The
dependency never changes a response. Enforce mode raises like AIFactory.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import get_settings
from server.database.engine import get_db
from server.database.models import OrgMember
from server.routes.organizations import ROLE_LEVELS
from server.services import audit_service, project_paths

logger = logging.getLogger(__name__)

__all__ = [
    "BodyProjectAccessChecker",
    "CamelProjectAccessChecker",
    "PlanAccessChecker",
    "ProjectAccessChecker",
    "TaskAccessChecker",
    "authorize_plan_session",
    "authorize_project",
    "authorize_project_for_user",
    "check_plan_session_access",
    "check_project_access",
    "is_service_principal",
    "plan_session_filter",
    "require_body_project_access",
    "require_camel_project_access",
    "require_plan_access",
    "require_project_access",
    "require_task_access",
]


def _auth_disabled() -> bool:
    """Whether global auth is off (dev mode); the dependency then no-ops."""
    try:
        return bool(get_settings().DISABLE_AUTH)
    except Exception:  # noqa: BLE001 - unreadable settings: auth stays on
        return False


def _enforcing() -> bool:
    return bool(get_settings().RBAC_ENFORCE_TASK_ROUTES)


def is_service_principal(user: dict[str, Any] | None) -> bool:
    """True for ``is_service`` principals only; a ``role`` claim never counts."""
    return isinstance(user, dict) and bool(user.get("is_service"))


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _check_role(role: str | None, minimum_role: str) -> None:
    if ROLE_LEVELS.get(role or "", -1) < ROLE_LEVELS.get(minimum_role, 0):
        raise _forbidden(
            f"This action requires at least '{minimum_role}' role in the project's organization."
        )


def check_project_access(
    user: dict[str, Any] | None,
    project: dict[str, Any] | None,
    membership: OrgMember | None,
    minimum_role: str = "viewer",
) -> None:
    """Pure rule. Raises ``HTTPException`` on denial, returns None on allow.

    No user 401; service principal allow; no project 404; project without
    ``org_id`` 403; API key for another org 403; non-member 403; role below
    ``minimum_role`` 403.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if is_service_principal(user):
        return
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    org_id = project.get("org_id")
    if not org_id:
        raise _forbidden("Project is not assigned to an organization")
    if user.get("api_key_id") and user.get("org_id") != org_id:
        raise _forbidden("This API key is scoped to another organization")
    if membership is None:
        raise _forbidden("You do not have access to this project")
    _check_role(membership.role, minimum_role)


async def _audit_denial(  # noqa: PLR0913 - one audit row's fields
    action: str,
    user: dict[str, Any] | None,
    resource_type: str,
    resource_id: str | None,
    org_id: str | None,
    minimum_role: str,
    detail: object,
    extra: dict[str, Any] | None = None,
) -> None:
    """Chained audit row on its own session (the request session is rolled back
    with a 403, #1919). Never raises."""
    try:
        await audit_service.log_audit_event_bg(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user.get("id") if isinstance(user, dict) else None,
            org_id=org_id,
            details={"detail": str(detail), "minimum_role": minimum_role, **(extra or {})},
        )
    except Exception:  # noqa: BLE001 - a broken audit must not change the decision
        logger.warning("rbac.audit_failed", exc_info=True)


async def _handle_denial(  # noqa: PLR0913 - keyword-only audit context
    exc: HTTPException,
    *,
    enforce: bool,
    user: dict[str, Any] | None,
    resource_type: str,
    resource_id: str | None,
    org_id: str | None,
    member: bool,
    minimum_role: str,
    db: AsyncSession,
) -> None:
    """The one shadow/enforce switch. Returns (allow) in shadow, raises in enforce."""
    action = audit_service.ACTION_GATE_REJECTED if member else audit_service.ACTION_AUTHZ_DENIED
    args = (action, user, resource_type, resource_id, org_id, minimum_role, exc.detail)
    # Free the request's connection before the audit takes a second one, in
    # both modes: two pooled connections per denial can stall the pool (#1936).
    with contextlib.suppress(Exception):
        await db.rollback()
    if enforce:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            await _audit_denial(*args)
        raise exc
    principal = user.get("id") if isinstance(user, dict) else None
    logger.warning(
        "rbac.would_deny principal=%s %s=%s status=%s detail=%s",
        principal, resource_type, resource_id, exc.status_code, exc.detail,
    )  # fmt: skip
    await _audit_denial(*args, extra={"shadow": True, "status": exc.status_code})


async def _project_decision(
    user: dict[str, Any] | None,
    project_id: str | None,
    db: AsyncSession,
    minimum_role: str,
    ctx: dict[str, Any],
) -> None:
    """Load project + membership and apply the rule; fills ``ctx`` for audit."""
    if is_service_principal(user):
        check_project_access(user, None, None, minimum_role)
        return None
    project = project_paths.load_projects().get(project_id) if project_id else None
    ctx["org_id"] = org_id = project.get("org_id") if project else None
    membership = None
    if isinstance(user, dict) and user.get("id") and org_id:
        result = await db.execute(
            select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.user_id == user["id"])
        )
        membership = result.scalar_one_or_none()
    ctx["member"] = membership is not None
    check_project_access(user, project, membership, minimum_role)
    return None


async def authorize_project_for_user(
    user: dict[str, Any] | None,
    project_id: str | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> str | None:
    """Always-enforcing variant for callers without a request (WebSockets).

    Returns the project's ``org_id`` (None for the service principal / auth
    disabled) for audit attribution.
    """
    if _auth_disabled():
        return None
    ctx: dict[str, Any] = {}
    try:
        await _project_decision(user, project_id, db, minimum_role, ctx)
    except HTTPException as exc:
        await _handle_denial(
            exc, enforce=True, user=user, resource_type="project",
            resource_id=project_id, org_id=ctx.get("org_id"),
            member=bool(ctx.get("member")), minimum_role=minimum_role, db=db,
        )  # fmt: skip
    return ctx.get("org_id")


class _AccessChecker:
    """Shared shadow/enforce path; subclasses add ``__call__`` with their path param."""

    resource_type = "project"

    def __init__(self, minimum_role: str = "viewer") -> None:
        self.minimum_role = minimum_role

    async def _decide(
        self, user: dict[str, Any] | None, key: str | None, db: AsyncSession, ctx: dict[str, Any]
    ) -> None:
        await _project_decision(user, key, db, self.minimum_role, ctx)

    async def check(
        self, user: dict[str, Any] | None, key: str | None, db: AsyncSession
    ) -> dict[str, Any] | None:
        if _auth_disabled():
            return (
                user
                if isinstance(user, dict)
                else {"id": "default", "role": "admin", "is_service": True}
            )
        ctx: dict[str, Any] = {}
        enforce = _safe_enforcing()  # decided once, before anything can raise
        try:
            await self._decide(user, key, db, ctx)
        except HTTPException as exc:
            await _handle_denial(
                exc, enforce=enforce, user=user, resource_type=self.resource_type,
                resource_id=key, org_id=ctx.get("org_id"),
                member=bool(ctx.get("member")), minimum_role=self.minimum_role, db=db,
            )  # fmt: skip
        except Exception:  # shadow mode fails open on lookup errors
            if enforce:
                raise
            logger.warning("rbac.check_failed (shadow, allowing)", exc_info=True)
        finally:
            # The handler may share this session (Depends cache): leave it usable.
            with contextlib.suppress(Exception):
                await db.rollback()
        return user

    async def _authorize(
        self, key: str, request: Request, db: AsyncSession
    ) -> dict[str, Any] | None:
        return await self.check(getattr(request.state, "user", None), key, db)


class ProjectAccessChecker(_AccessChecker):
    """Dependency: authorize the caller against a ``project_id`` path param."""

    async def __call__(
        self, project_id: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> dict[str, Any] | None:
        return await self._authorize(project_id, request, db)


async def authorize_project(
    user: dict[str, Any] | None,
    project_id: str | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> None:
    """In-handler check (rmux, MCP proxies): shadow or enforce, like the dependencies."""
    await ProjectAccessChecker(minimum_role).check(user, project_id, db)


class CamelProjectAccessChecker(_AccessChecker):
    """Dependency for routes with a camelCase project path parameter."""

    async def __call__(
        self,
        projectId: str,  # noqa: N803
        request: Request,
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> dict[str, Any] | None:
        return await self._authorize(projectId, request, db)


class BodyProjectAccessChecker(_AccessChecker):
    """Dependency for routes with a project ID in the JSON body."""

    async def __call__(
        self, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - invalid JSON has no project key
            body = None
        key = body.get("project_id") if isinstance(body, dict) else None
        return await self.check(getattr(request.state, "user", None), key, db)


def _safe_enforcing() -> bool:
    try:
        return _enforcing()
    except Exception:  # noqa: BLE001 - unreadable flag means shadow
        return False


class TaskAccessChecker(_AccessChecker):
    """Keyed on a ``task_id`` path param of the form ``project_id:spec_id``."""

    async def __call__(
        self, task_id: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> dict[str, Any] | None:
        return await self._authorize(task_id.split(":", 1)[0], request, db)


class PlanAccessChecker(_AccessChecker):
    """Plan-pipeline routes have no project path param: the caller needs the
    minimum role in at least one org (an ``acw_`` key: in its own org)."""

    resource_type = "plan_session"

    async def _decide(
        self, user: dict[str, Any] | None, _key: str | None, db: AsyncSession, ctx: dict[str, Any]
    ) -> None:
        if not user:
            check_project_access(user, None, None)  # raises 401
            return None
        if is_service_principal(user):
            return None
        query = select(OrgMember.role).where(OrgMember.user_id == user.get("id"))
        if user.get("api_key_id"):
            ctx["org_id"] = user.get("org_id")
            query = query.where(OrgMember.org_id == user.get("org_id"))
        roles = (await db.execute(query)).scalars().all()
        ctx["member"] = bool(roles)
        if not roles:
            raise _forbidden("You do not have access to any organization")
        _check_role(max(roles, key=lambda r: ROLE_LEVELS.get(r, -1)), self.minimum_role)
        return None

    async def __call__(
        self, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> dict[str, Any] | None:
        return await self._authorize(request.url.path, request, db)


def require_project_access(minimum_role: str = "viewer") -> ProjectAccessChecker:
    return ProjectAccessChecker(minimum_role)


def require_camel_project_access(minimum_role: str = "viewer") -> CamelProjectAccessChecker:
    return CamelProjectAccessChecker(minimum_role)


def require_body_project_access(minimum_role: str = "viewer") -> BodyProjectAccessChecker:
    return BodyProjectAccessChecker(minimum_role)


def require_task_access(minimum_role: str = "viewer") -> TaskAccessChecker:
    return TaskAccessChecker(minimum_role)


def require_plan_access(minimum_role: str = "viewer") -> PlanAccessChecker:
    return PlanAccessChecker(minimum_role)


def check_plan_session_access(
    user: dict[str, Any] | None,
    org_id: str | None,
    role: str | None,
    minimum_role: str = "viewer",
) -> None:
    """Pure rule for per-session plan access (#968)."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if is_service_principal(user):
        return None
    if not org_id:
        raise _forbidden("Plan session is not assigned to an organization")
    if user.get("api_key_id") and user.get("org_id") != org_id:
        raise _forbidden("This API key is scoped to another organization")
    if role is None:
        raise _forbidden("You do not have access to this plan session")
    _check_role(role, minimum_role)
    return None


def _key_outside_org(user: dict[str, Any] | None, org_id: str | None) -> bool:
    """An org-scoped API key on a session that is not known to be in its org.

    An unknown org (None) counts: org keys fail closed, not behind the flag (#968).
    """
    return bool(
        isinstance(user, dict)
        and user.get("api_key_id")
        and user.get("org_id")
        and user.get("org_id") != org_id
    )


async def _member_roles(user: dict[str, Any] | None, db: AsyncSession) -> dict[str, str]:
    """Map of org_id -> role for non-service users with an id."""
    if not isinstance(user, dict) or not user.get("id") or is_service_principal(user):
        return {}
    rows = (
        await db.execute(
            select(OrgMember.org_id, OrgMember.role).where(OrgMember.user_id == user["id"])
        )
    ).all()
    return {str(org): str(role) for org, role in rows}


async def authorize_plan_session(
    user: dict[str, Any] | None,
    session_id: str,
    org_id: str | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> None:
    """Authorize reading a single plan session; translates 403 denials to 404 (#968)."""
    if _auth_disabled() or is_service_principal(user):
        return None
    enforce = _safe_enforcing() or _key_outside_org(user, org_id)
    if not org_id and not enforce:
        return None
    roles = await _member_roles(user, db) if org_id else {}
    try:
        check_plan_session_access(user, org_id, roles.get(org_id or ""), minimum_role)
    except HTTPException as exc:
        try:
            await _handle_denial(
                exc,
                enforce=enforce,
                user=user,
                resource_type="plan_session",
                resource_id=session_id,
                org_id=org_id,
                member=bool(org_id and org_id in roles),
                minimum_role=minimum_role,
                db=db,
            )
        except HTTPException as denied:
            if denied.status_code == status.HTTP_403_FORBIDDEN:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"unknown session '{session_id}'",
                ) from None
            raise
    return None


async def plan_session_filter(
    user: dict[str, Any] | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> Callable[[str | None], bool] | None:
    """Return an org_id filter predicate for list_sessions, or None when unfiltered (#968)."""
    if _auth_disabled() or is_service_principal(user):
        return None
    if not _safe_enforcing():
        if isinstance(user, dict) and user.get("api_key_id") and user.get("org_id"):
            key_org = user["org_id"]
            return lambda org: org == key_org  # unknown org: hidden from org keys
        return None
    roles = await _member_roles(user, db)

    def _allow(org: str | None) -> bool:
        try:
            check_plan_session_access(user, org, roles.get(org or ""), minimum_role)
        except HTTPException:
            return False
        return True

    return _allow
