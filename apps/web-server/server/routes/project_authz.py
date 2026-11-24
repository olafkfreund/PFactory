"""Project, task, and plan authorization for web routes."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plan.repo_ref import PROVIDER_GIT_HOST, parse_repo_ref
from server.config import get_settings
from server.database.engine import get_db
from server.database.models import Organization, OrgMember
from server.routes.organizations import ROLE_LEVELS
from server.services import audit_service, project_paths

logger = logging.getLogger(__name__)

__all__ = [
    "BodyProjectAccessChecker",
    "CamelProjectAccessChecker",
    "PlanAccessChecker",
    "ProjectAccess",
    "ProjectAccessChecker",
    "TaskAccessChecker",
    "authorize_plan_ingest",
    "authorize_plan_session",
    "authorize_project",
    "authorize_project_for_user",
    "check_plan_session_access",
    "check_project_access",
    "creator_org",
    "is_service_principal",
    "plan_session_filter",
    "require_body_project_access",
    "require_camel_project_access",
    "require_path_project_role",
    "require_plan_access",
    "require_project_access",
    "require_project_role",
    "require_repo_project_role",
    "require_task_access",
    "resolve_default_org_id",
    "scope_role_ceiling",
    "visible_project_ids",
]


def _auth_disabled() -> bool:
    """Whether global auth is off (dev mode)."""
    try:
        return bool(get_settings().DISABLE_AUTH)
    except Exception:  # noqa: BLE001 - unreadable settings: auth stays on
        return False


def is_service_principal(user: dict[str, Any] | None) -> bool:
    """True for ``is_service`` principals only; a ``role`` claim never counts."""
    return isinstance(user, dict) and bool(user.get("is_service")) and not user.get("api_key_id")


def _org_service_key(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("api_key_id") and user.get("org_id") and not user.get("id"))


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _check_role(role: str | None, minimum_role: str) -> None:
    if ROLE_LEVELS.get(role or "", -1) < ROLE_LEVELS.get(minimum_role, 0):
        raise _forbidden(
            f"This action requires at least '{minimum_role}' role in the project's organization."
        )


@dataclass(frozen=True)
class ProjectAccess:
    project_id: str
    org_id: str | None
    role: str
    user_id: str


def scope_role_ceiling(principal: dict[str, Any]) -> str | None:
    """Maximum project role allowed by a token's scopes."""
    if "api_key_id" not in principal and "scopes" not in principal:
        return "owner"
    scopes = set(principal.get("scopes") or ())
    if scopes & {"task:merge", "admin"}:
        return "admin"
    if scopes & {"project:write", "task:write", "plan:write", "mcp:write"}:
        return "member"
    if "api" in scopes and not scopes.intersection(
        {"mcp:read", "project:read", "task:read", "plan:read"}
    ):
        return "member"
    if scopes & {"mcp:read", "project:read", "task:read", "plan:read"}:
        return "viewer"
    return None


def check_project_access(
    user: dict[str, Any] | None,
    project: dict[str, Any] | None,
    membership: OrgMember | None,
    minimum_role: str = "viewer",
) -> None:
    """Pure grant and scope rule used by project authorization."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    org_id = project.get("org_id") or (
        "default" if _auth_disabled() or is_service_principal(user) else None
    )
    if not org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    if is_service_principal(user):
        return
    if _auth_disabled() and user.get("id") == "default":
        return
    if user.get("org_id") and user["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Project not found")
    if _org_service_key(user):
        ceiling = scope_role_ceiling(user)
        if ceiling and ROLE_LEVELS[ceiling] >= ROLE_LEVELS[minimum_role]:
            return
        raise _forbidden(f"Requires {minimum_role} role on project")
    if membership is None or ROLE_LEVELS.get(membership.role, -1) < ROLE_LEVELS["viewer"]:
        raise HTTPException(status_code=404, detail="Project not found")
    ceiling = scope_role_ceiling(user)
    if (
        ceiling is None
        or min(ROLE_LEVELS[membership.role], ROLE_LEVELS[ceiling]) < ROLE_LEVELS[minimum_role]
    ):
        raise _forbidden(f"Requires {minimum_role} role on project")


async def require_project_role(
    principal: dict[str, Any] | None,
    project_id: str | None,
    min_role: str = "viewer",
    *,
    db: AsyncSession,
) -> ProjectAccess:
    """Authorize one registered project; hidden projects share the unknown-project 404."""
    try:
        project = project_paths.load_projects().get(project_id) if project_id else None
    except Exception:  # noqa: BLE001 - an unreadable registry must fail closed
        raise HTTPException(status_code=503, detail="Project registry unavailable") from None
    org_id = project.get("org_id") if project else None
    if project and not org_id and is_service_principal(principal) and not _auth_disabled():
        org_id = await resolve_default_org_id(db)
    membership = None
    if (
        principal
        and principal.get("id")
        and org_id
        and not is_service_principal(principal)
        and not (_auth_disabled() and principal["id"] == "default")
    ):
        membership = (
            await db.execute(
                select(OrgMember).where(
                    OrgMember.org_id == org_id, OrgMember.user_id == principal["id"]
                )
            )
        ).scalar_one_or_none()
    try:
        check_project_access(principal, project, membership, min_role)
    except HTTPException as exc:
        await db.rollback()
        if exc.status_code in (403, 404):
            action = (
                audit_service.ACTION_GATE_REJECTED
                if membership
                else audit_service.ACTION_AUTHZ_DENIED
            )
            await _audit_denial(
                action, principal, "project", project_id, org_id, min_role, exc.detail
            )
        raise
    if (
        principal is None
        or project_id is None
        or (org_id is None and not is_service_principal(principal) and not _auth_disabled())
    ):
        raise HTTPException(status_code=404, detail="Project not found")
    role = (
        "owner"
        if is_service_principal(principal)
        or (_auth_disabled() and principal.get("id") == "default")
        else (scope_role_ceiling(principal) or "viewer")
        if _org_service_key(principal)
        else min(
            membership.role if membership else "owner",
            scope_role_ceiling(principal) or "viewer",
            key=ROLE_LEVELS.__getitem__,
        )
    )
    return ProjectAccess(project_id, org_id, role, principal.get("id") or "")


async def visible_project_ids(
    principal: dict[str, Any] | None, db: AsyncSession, min_role: str = "viewer"
) -> set[str]:
    """Filter the registry with one membership query."""
    try:
        projects = project_paths.load_projects()
    except Exception:  # noqa: BLE001 - an unreadable registry must fail closed
        raise HTTPException(status_code=503, detail="Project registry unavailable") from None
    if not principal:
        return set()
    if is_service_principal(principal) or (_auth_disabled() and principal.get("id") == "default"):
        return set(projects)
    ceiling = scope_role_ceiling(principal)
    if ceiling is None or ROLE_LEVELS[ceiling] < ROLE_LEVELS[min_role]:
        return set()
    if _org_service_key(principal):
        return {
            pid for pid, project in projects.items() if project.get("org_id") == principal["org_id"]
        }
    roles = await _member_roles(principal, db)
    return {
        pid
        for pid, project in projects.items()
        if (org := project.get("org_id") or ("default" if _auth_disabled() else None))
        and (not principal.get("org_id") or principal["org_id"] == org)
        and ROLE_LEVELS.get(roles.get(org, ""), -1) >= ROLE_LEVELS[min_role]
    }


async def require_path_project_role(
    principal: dict[str, Any] | None, path: str | None, min_role: str, db: AsyncSession
) -> ProjectAccess:
    return await require_project_role(
        principal, project_paths.project_id_for_path(path), min_role, db=db
    )


async def require_repo_project_role(  # noqa: PLR0913 - provider is part of repository identity
    principal: dict[str, Any] | None,
    repo: str | None,
    min_role: str,
    db: AsyncSession,
    *,
    for_intake: bool = False,
    provider: str = "github",
    project_id: str | None = None,
) -> ProjectAccess:
    """Authorize a registered owner/repo, or an org-backed intake."""
    target = canonical_repo_ref(repo, provider) if repo else None
    if target is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        projects = project_paths.load_projects()
    except Exception:  # noqa: BLE001 - unreadable registry fails closed
        raise HTTPException(status_code=503, detail="Project registry unavailable") from None

    def matches_project(project: dict[str, Any]) -> bool:
        settings = project.get("settings") or {}
        project_provider = settings.get("gitProvider", "github")
        base_host = urlsplit(settings.get("gitBaseUrl") or "").hostname
        repo_value = repo or ""
        parsed = parse_repo_ref(repo_value)
        use_base = (
            base_host
            and provider == project_provider
            and parsed
            and parsed[0] == provider
            and "://" not in repo_value
            and "@" not in repo_value
        )
        requested = (
            canonical_repo_ref(parsed[1], base_host or provider) if use_base and parsed else target
        )
        return any(
            canonical_repo_ref(str(value), base_host or project_provider) == requested
            for value in (
                project.get("repo"),
                project.get("github_repo"),
                settings.get("githubRepo"),
                settings.get("gitRepo"),
                project.get("gitUrl"),
                project.get("git_url"),
                project.get("clonedFrom"),
            )
            if value
        )

    matches = [pid for pid, project in projects.items() if matches_project(project)]
    if project_id is not None:
        if project_id not in matches:
            raise HTTPException(status_code=404, detail="Project not found")
        matches = [project_id]
    visible = await visible_project_ids(principal, db)
    for pid in matches:
        if pid in visible:
            return await require_project_role(principal, pid, min_role, db=db)
    if matches:
        await _audit_denial(
            audit_service.ACTION_AUTHZ_DENIED,
            principal,
            "repository",
            repo,
            projects[matches[0]].get("org_id"),
            min_role,
            "Project not found",
        )
    if not matches and for_intake and principal:
        if is_service_principal(principal):
            return ProjectAccess(
                repo or "",
                await resolve_default_org_id(db) if isinstance(db, AsyncSession) else None,
                "owner",
                principal["id"],
            )
        if _auth_disabled() and principal.get("id") == "default":
            return ProjectAccess(repo or "", "default", "owner", "default")
        org_id = principal.get("org_id") or await creator_org(principal, db)
        roles = await _member_roles(principal, db)
        ceiling = scope_role_ceiling(principal)
        if (
            org_id
            and ceiling
            and ROLE_LEVELS[ceiling] >= ROLE_LEVELS[min_role]
            and (
                ROLE_LEVELS.get(roles.get(org_id, ""), -1) >= ROLE_LEVELS[min_role]
                or _org_service_key(principal)
            )
        ):
            return ProjectAccess(repo or "", org_id, min_role, principal.get("id") or "")
    raise HTTPException(status_code=404, detail="Project not found")


def canonical_repo_ref(value: str | None, provider: str = "github") -> tuple[str, str] | None:
    if not value:
        return None
    parsed = parse_repo_ref(value)
    if parsed and parsed[1] != value:
        provider, value = parsed
    value = value.rstrip("/").removesuffix(".git")
    if "://" in value:
        url = urlsplit(value)
        provider = (url.hostname or "").lower()
        value = url.path
    elif "@" in value and ":" in value:
        host, value = value.split(":", 1)
        provider = host.rsplit("@", 1)[-1].lower()
    parts = value.strip("/").split("/")
    host = PROVIDER_GIT_HOST.get(provider, provider)
    return (host, "/".join(parts)) if len(parts) >= 2 and all(parts) else None  # noqa: PLR2004


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
        if exc.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND):
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
    """Compatibility entry for the bare-spec checker."""
    access = await require_project_role(user, project_id, minimum_role, db=db)
    ctx["org_id"] = access.org_id
    ctx["member"] = True


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
    access = await require_project_role(
        user or ({"id": "default"} if _auth_disabled() else None), project_id, minimum_role, db=db
    )
    return access.org_id


class _AccessChecker:
    """Shared shadow/enforce path; subclasses add ``__call__`` with their path param."""

    resource_type = "project"

    def __init__(self, minimum_role: str = "viewer") -> None:
        self.minimum_role = minimum_role

    async def _decide(
        self, user: dict[str, Any] | None, key: str | None, db: AsyncSession, ctx: dict[str, Any]
    ) -> None:
        access = await require_project_role(user, key, self.minimum_role, db=db)
        ctx["org_id"] = access.org_id

    async def check(
        self, user: dict[str, Any] | None, key: str | None, db: AsyncSession
    ) -> dict[str, Any] | None:
        if _auth_disabled() and user is None:
            user = {"id": "default"}
        await self._decide(user, key, db, {})
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
        if _org_service_key(user):
            ceiling = scope_role_ceiling(user)
            if ceiling and ROLE_LEVELS[ceiling] >= ROLE_LEVELS[self.minimum_role]:
                return None
            raise _forbidden(f"Requires {self.minimum_role} role on project")
        query = select(OrgMember.role).where(OrgMember.user_id == user.get("id"))
        if user.get("api_key_id"):
            ctx["org_id"] = user.get("org_id")
            query = query.where(OrgMember.org_id == user.get("org_id"))
        roles = (await db.execute(query)).scalars().all()
        ctx["member"] = bool(roles)
        if not roles:
            await db.rollback()
            await _audit_denial(
                audit_service.ACTION_AUTHZ_DENIED,
                user,
                "plan_session",
                None,
                ctx.get("org_id"),
                self.minimum_role,
                "You do not have access to any organization",
            )
            raise _forbidden("You do not have access to any organization")
        ceiling = scope_role_ceiling(user)
        if (
            ceiling is None
            or min(
                ROLE_LEVELS[max(roles, key=lambda r: ROLE_LEVELS.get(r, -1))], ROLE_LEVELS[ceiling]
            )
            < ROLE_LEVELS[self.minimum_role]
        ):
            await db.rollback()
            await _audit_denial(
                audit_service.ACTION_GATE_REJECTED,
                user,
                "plan_session",
                None,
                ctx.get("org_id"),
                self.minimum_role,
                f"Requires {self.minimum_role} role on project",
            )
            raise _forbidden(f"Requires {self.minimum_role} role on project")
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
    if is_service_principal(user) or (_auth_disabled() and user.get("id") == "default"):
        return None
    if not org_id:
        raise HTTPException(status_code=404, detail="Plan session not found")
    if user.get("org_id") and user.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="Plan session not found")
    if role is None:
        raise HTTPException(status_code=404, detail="Plan session not found")
    ceiling = scope_role_ceiling(user)
    if ceiling is None or min(ROLE_LEVELS[role], ROLE_LEVELS[ceiling]) < ROLE_LEVELS[minimum_role]:
        raise _forbidden(f"Requires {minimum_role} role on project")
    return None


async def _member_roles(user: dict[str, Any] | None, db: AsyncSession) -> dict[str, str]:
    """Map of org_id -> role for non-service users with an id."""
    if _org_service_key(user):
        return {str((user or {}).get("org_id")): "owner"}
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
    org_of: Callable[[], str | None],
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> None:
    """Authorize reading a single plan session; translates 403 denials to 404 (#968).

    ``org_of`` is called only past the auth-disabled / service early returns: the
    cockpit polls these reads, and resolving an org can hit the registry and DB.
    """
    if _auth_disabled() and user is None:
        user = {"id": "default"}
    if is_service_principal(user) or (_auth_disabled() and user and user.get("id") == "default"):
        return None
    org_id = org_of()
    roles = await _member_roles(user, db) if org_id else {}
    try:
        check_plan_session_access(user, org_id, roles.get(org_id or ""), minimum_role)
    except HTTPException as exc:
        try:
            await _handle_denial(
                exc,
                enforce=True,
                user=user,
                resource_type="plan_session",
                resource_id=session_id,
                org_id=org_id,
                member=bool(org_id and org_id in roles),
                minimum_role=minimum_role,
                db=db,
            )
        except HTTPException:
            raise
    return None


async def plan_session_filter(
    user: dict[str, Any] | None,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> Callable[[str | None], bool] | None:
    """Return an org_id filter predicate for list_sessions, or None when unfiltered (#968)."""
    if is_service_principal(user) or (_auth_disabled() and user and user.get("id") == "default"):
        return None
    roles = await _member_roles(user, db)

    def _allow(org: str | None) -> bool:
        try:
            check_plan_session_access(user, org, roles.get(org or ""), minimum_role)
        except HTTPException:
            return False
        return True

    return _allow


def _org_key(user: dict[str, Any] | None) -> str | None:
    """The org of an org-scoped ``acw_`` key, else None."""
    if isinstance(user, dict) and user.get("api_key_id") and user.get("org_id"):
        return str(user["org_id"])
    return None


async def authorize_plan_ingest(
    user: dict[str, Any] | None,
    repo_org: str | None,
    db: AsyncSession,
    minimum_role: str = "member",
) -> str | None:
    """Check a plan ingest against its repo's org; return the org to stamp (#968).

    An org key on a repo in another org: 403 always (audited). An org key on a
    repo with no known org stamps the key's own org, so it can read its session
    back. Humans need ``minimum_role`` in the repo's org, in shadow or enforce
    mode, and get the repo's org (or none). Never stamps another org's id on a
    session an org key creates.
    """
    if _auth_disabled():
        return repo_org or "default"
    if is_service_principal(user):
        return repo_org or _org_key(user)
    org_id = repo_org or await creator_org(user, db)
    roles = await _member_roles(user, db)
    try:
        check_plan_session_access(user, org_id, roles.get(org_id or ""), minimum_role)
    except HTTPException as exc:
        await _handle_denial(
            exc, enforce=True, user=user, resource_type="plan_session",
            resource_id=None, org_id=org_id, member=bool(org_id and org_id in roles),
            minimum_role=minimum_role, db=db,
        )  # fmt: skip
    return org_id


async def creator_org(user: dict[str, Any] | None, db: AsyncSession) -> str | None:
    """The org to stamp on a project this principal creates (#1036).

    An org-scoped key: its org. A human: their org when they belong to exactly
    one. The service principal has no org and nothing here resolves a single-org
    default, so it, and a human in several orgs, get None and a log line; an
    org-less project is 403 for non-service callers until an org is set.
    """
    if _auth_disabled():
        return "default"
    if is_service_principal(user):
        return None
    if key_org := _org_key(user):
        return key_org
    roles = await _member_roles(user, db)
    if len(roles) == 1:
        return next(iter(roles))
    logger.warning(
        "project.org_unstamped principal=%s orgs=%d",
        user.get("id") if isinstance(user, dict) else None, len(roles),
    )  # fmt: skip
    return None


async def resolve_default_org_id(db: AsyncSession) -> str | None:
    """Resolve a service principal's implicit org only when it is unambiguous."""
    org_ids = (await db.execute(select(Organization.id).limit(2))).scalars().all()
    return org_ids[0] if len(org_ids) == 1 else None
