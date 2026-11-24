"""Proxy router for the stdio-MCP control plane (Issue #154).

Re-exposes the 15 operations the stdio MCP client exercises today,
under ``/api/mcp-stdio/*`` — each route gated by ``acw_`` key + scope.

Every handler delegates to the same service the regular REST routes
use. The proxy adds nothing but auth + audit: no URL rewriting, no
payload transformation, no extra hop. If a delegated handler raises
``HTTPException``, it propagates as-is (and no audit row is written).

Why import-and-call instead of HTTP-forwarding to the existing routes:
- One process, no extra hop, no risk of an MCP request looping back
  through ``TokenAuthMiddleware`` which would reject the ``acw_`` key
  on the regular REST surface.
- Same code path, so behavior identity is mechanical.

Audit logging (Epic #50 acceptance criterion #2):
Each write route fires a background ``log_audit_event_bg`` call after
the delegated handler returns successfully. Actions land under the
``mcp.*`` namespace (``mcp.task.start``, ``mcp.project.create``, etc.)
so MCP-initiated mutations stay distinguishable from equivalent
UI-driven actions in the audit log.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request as FastAPIRequest,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from client_errors import client_error
from plan.ingest.artifacts import ArtifactIngestError
from plan.service import SERVICE, EmitInProgressError, PlanServiceError
from server.database.engine import get_db
from server.routes.organizations import ROLE_LEVELS
from server.routes.plan_meta import categories as plan_categories
from server.routes.plan_pipeline import (
    ApproveBody,
    EmitContractBody,
    IngestTextBody,
    RejectBody,
    _attribution,
    approve as plan_approve,
    audit_pack as plan_audit_pack,
    emit_contract as plan_emit_contract,
    get_session as plan_get_session,
    ingest_text as plan_ingest_text,
    ingest_upload as plan_ingest_upload,
    list_sessions as plan_list_sessions,
    process as plan_process,
    reject as plan_reject,
)
from server.routes.project_authz import (
    PlanAccessChecker,
    _member_roles,
    authorize_project,
    authorize_project_for_user,
    require_repo_project_role,
    resolve_default_org_id,
    scope_role_ceiling,
    visible_project_ids,
)
from server.routes.projects import ProjectCreate, create_project
from server.services import project_paths
from server.services.audit_service import (
    ACTION_MCP_PLAN_APPROVE,
    ACTION_MCP_PLAN_EMIT,
    ACTION_MCP_PLAN_INGEST,
    ACTION_MCP_PLAN_PROCESS,
    ACTION_MCP_PLAN_REJECT,
)
from server.tenancy import resolve_tenant

from ..mcp_remote.auth import AuthenticatedKey
from ..services.audit_service import (
    ACTION_MCP_PROJECT_CREATE,
    ACTION_MCP_TASK_APPROVE_PLAN,
    ACTION_MCP_TASK_CREATE_AND_RUN,
    ACTION_MCP_TASK_CREATE_PR,
    ACTION_MCP_TASK_MERGE,
    ACTION_MCP_TASK_RECOVER,
    ACTION_MCP_TASK_START,
    ACTION_MCP_TASK_STOP,
    log_audit_event_bg,
)
from .auth import (
    MCP_READ_SCOPE,
    PLAN_WRITE_SCOPE,
    PROJECT_WRITE_SCOPE,
    TASK_MERGE_SCOPE,
    TASK_WRITE_SCOPE,
    _LegacyAdminKey,
    require_acw_scope,
)

router = APIRouter(prefix="/api/mcp-stdio", tags=["MCP (stdio)"])
logger = logging.getLogger(__name__)
_MAX_ARTIFACT_BYTES = 256 * 1024


def _principal(key: AuthenticatedKey | _LegacyAdminKey) -> dict[str, Any]:
    if isinstance(key, AuthenticatedKey):
        return {
            "id": key.user_id,
            "api_key_id": key.key_id,
            "org_id": key.org_id,
            "scopes": tuple(key.scopes),
            "is_service": bool(getattr(key, "is_service", False)),
        }
    return {"id": "default", "is_service": True}


def _key_projects(key: AuthenticatedKey | _LegacyAdminKey) -> dict[str, Any] | None:
    """The project map for an org-scoped key; None when the key has no org to check.

    Fails closed (503): an unreadable registry must not let an org key through.
    """
    if not isinstance(key, AuthenticatedKey) or not key.org_id:
        return None
    try:
        projects: dict[str, Any] = project_paths.load_projects()
        return projects
    except Exception:  # noqa: BLE001 - any registry error fails closed
        logger.warning("rbac.projects_unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="Project registry unavailable") from None


def _foreign_ids(key: AuthenticatedKey, projects: dict[str, Any]) -> set[str]:
    return {
        pid
        for pid, pdata in projects.items()
        if isinstance(pdata, dict) and pdata.get("org_id") and pdata.get("org_id") != key.org_id
    }


def _foreign_projects(key: AuthenticatedKey | _LegacyAdminKey) -> set[str]:
    """Project ids in another org (not behind the flag: org-A keys never read org B)."""
    projects = _key_projects(key)
    if projects is None or not isinstance(key, AuthenticatedKey):
        return set()
    return _foreign_ids(key, projects)


async def _authorize_key(
    key: AuthenticatedKey | _LegacyAdminKey,
    project_id: str,
    db: AsyncSession,
    minimum_role: str = "viewer",
) -> None:
    """Org key on an unknown or another org's project: always 404/403 (like
    TFactory #1465). Role check: shadow or enforce."""
    principal = _principal(key)
    projects = _key_projects(key)
    if (
        projects is not None
        and isinstance(key, AuthenticatedKey)
        and (project_id not in projects or project_id in _foreign_ids(key, projects))
    ):
        await authorize_project_for_user(principal, project_id, db, minimum_role)
    await authorize_project(principal, project_id, db, minimum_role)


async def _read_json_body(request: FastAPIRequest) -> dict[str, Any]:
    """Read request body, return {} if empty."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail="Expected a JSON object")
        return parsed
    except json.JSONDecodeError:
        return {}


async def _audit_mcp_write(
    key: AuthenticatedKey | _LegacyAdminKey,
    action: str,
    resource_type: str,
    resource_id: str | None,
    request: FastAPIRequest,
    details: dict | None = None,
) -> None:
    """Fire-and-forget background audit log entry for an MCP write.

    Uses ``log_audit_event_bg`` (self-managed session) so the route
    handler doesn't need a ``Depends(get_db)`` and so a slow audit
    write doesn't block the response. Failures inside the bg call
    are swallowed by audit_service's try/except wrapper — never
    crashes the route.

    The ``key_id`` from the auth key goes into ``details`` so
    operators can correlate audit rows back to a specific minted
    key (useful when investigating a compromised key).
    """
    enriched_details = dict(details or {})
    enriched_details["mcp_key_id"] = (
        key.key_id if isinstance(key, AuthenticatedKey) else "legacy-admin"
    )
    client_ip = request.client.host if request.client else None
    await log_audit_event_bg(
        user_id=key.user_id,
        org_id=key.org_id if isinstance(key, AuthenticatedKey) else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=enriched_details,
        ip=client_ip,
    )


# =============================================================================
# Read operations — mcp:read
# =============================================================================


@router.get("/projects")
async def proxy_list_projects(
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    from ..routes.projects import list_projects

    request.state.user = _principal(key)
    visible = await visible_project_ids(request.state.user, db)
    return [project for project in await list_projects(request, db) if project["id"] in visible]


@router.get("/tasks")
async def proxy_list_tasks(
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    from server.routes.tasks import list_tasks

    request.state.user = _principal(key)
    if project_id:
        await _authorize_key(key, project_id, db)
    else:
        visible = await visible_project_ids(request.state.user, db)
    result = await list_tasks(request, db, project_id=project_id, status=status)  # type: ignore[arg-type]
    if not project_id:
        tasks = [task for task in result.tasks if task.project_id in visible]
        result = result.model_copy(update={"tasks": tasks, "total": len(tasks)})
    return result


@router.get("/tasks/running")
async def proxy_get_running_tasks(
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    from server.routes.execution import get_running_tasks

    request.state.user = _principal(key)
    visible = await visible_project_ids(request.state.user, db)
    result = await get_running_tasks(request, db)
    tasks = [task for task in result.tasks if task.split(":", 1)[0] in visible]
    return result.model_copy(update={"tasks": tasks, "count": len(tasks)})


@router.get("/tasks/{task_id}")
async def proxy_get_task(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db)
    from ..routes.tasks import get_task

    return await get_task(task_id)


@router.get("/tasks/{task_id}/status")
async def proxy_get_task_status(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db)
    from ..routes.execution import get_task_status

    return await get_task_status(task_id)


@router.get("/tasks/{task_id}/logs")
async def proxy_get_task_logs(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    # #906: get_task_logs takes no tail and no MCP tool sends one; ?tail= is ignored.
    await _authorize_key(key, task_id.split(":", 1)[0], db)
    from ..routes.tasks import get_task_logs

    return await get_task_logs(task_id)


@router.get("/tasks/{task_id}/worktree/diff")
async def proxy_get_worktree_diff(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db)
    from ..routes.tasks import get_worktree_diff

    return await get_worktree_diff(task_id)


# =============================================================================
# Project mutation — project:write
# =============================================================================


@router.post("/projects", status_code=201)
async def proxy_add_project(
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(PROJECT_WRITE_SCOPE)),
):
    body = await _read_json_body(request)
    org_id = key.org_id if isinstance(key, AuthenticatedKey) else await resolve_default_org_id(db)
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, None, db)
    grant = (await _member_roles(principal, db)).get(org_id or "", "")
    if isinstance(key, AuthenticatedKey) and (
        not org_id
        or ROLE_LEVELS.get(grant, -1) < ROLE_LEVELS["member"]
        or ROLE_LEVELS.get(scope_role_ceiling(principal) or "", -1) < ROLE_LEVELS["member"]
    ):
        raise HTTPException(status_code=403, detail="Project membership required")
    result = await create_project(ProjectCreate(**body), org_id)
    # ``add_project`` returns the project dict with an ``id`` field.
    project_id = result.get("id") if isinstance(result, dict) else None
    await _audit_mcp_write(
        key,
        ACTION_MCP_PROJECT_CREATE,
        "project",
        project_id,
        request,
    )
    return result


# =============================================================================
# Task mutation — task:write
# =============================================================================


@router.post("/tasks/create-and-run")
async def proxy_create_and_run_task(  # noqa: PLR0913 - one param per query field
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: str = Query(...),
    title: str = Query(...),
    description: str = Query(...),
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
    await _authorize_key(key, project_id, db, "member")
    from ..routes.execution import StartTaskRequest, create_and_run_task

    body = await _read_json_body(request)
    result = await create_and_run_task(project_id, title, description, StartTaskRequest(**body))
    task_id = result.get("task_id") if isinstance(result, dict) else None
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        "task",
        task_id,
        request,
        details={"project_id": project_id, "title": title},
    )
    return result


@router.post("/tasks/{task_id}/start")
async def proxy_start_task(
    task_id: str,
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db, "member")
    from ..routes.execution import StartTaskRequest, start_task

    body = await _read_json_body(request)
    result = await start_task(task_id, StartTaskRequest(**body), request)
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_START,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/stop")
async def proxy_stop_task(
    task_id: str,
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db, "member")
    from ..routes.execution import stop_task

    result = await stop_task(task_id)
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_STOP,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/recover")
async def proxy_recover_task(
    task_id: str,
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db, "member")
    from ..routes.execution import RecoverTaskRequest, recover_task

    body = await _read_json_body(request)
    result = await recover_task(task_id, RecoverTaskRequest(**body))
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_RECOVER,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/approve-plan")
async def proxy_approve_plan(
    task_id: str,
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db, "member")
    from ..routes.tasks import ApprovePlanRequest, approve_plan

    body = await _read_json_body(request)
    result = await approve_plan(task_id, ApprovePlanRequest(**body))
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_APPROVE_PLAN,
        "task",
        task_id,
        request,
    )
    return result


# =============================================================================
# PR / merge — task:merge (higher blast radius)
# =============================================================================


@router.post("/tasks/{task_id}/worktree/create-pr")
async def proxy_create_pr(
    task_id: str,
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(TASK_MERGE_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db, "member")
    from ..routes.tasks import CreatePRFromTaskOptions, create_pr_from_task

    body = await _read_json_body(request)
    request.state.user = _principal(key)
    result = await create_pr_from_task(
        task_id, request, CreatePRFromTaskOptions(**body) if body else None
    )
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_CREATE_PR,
        "task",
        task_id,
        request,
    )
    return result


@router.post("/tasks/{task_id}/worktree/merge")
async def proxy_merge_worktree(
    task_id: str,
    request: FastAPIRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    key=Depends(require_acw_scope(TASK_MERGE_SCOPE)),
):
    await _authorize_key(key, task_id.split(":", 1)[0], db, "member")
    from ..routes.tasks import WorktreeMergeOptions, merge_worktree

    body = await _read_json_body(request)
    request.state.user = _principal(key)
    result = await merge_worktree(task_id, request, WorktreeMergeOptions(**body) if body else None)
    await _audit_mcp_write(
        key,
        ACTION_MCP_TASK_MERGE,
        "task",
        task_id,
        request,
    )
    return result


# =============================================================================
# Plan pipeline (#694)
#
# The plan MCP tools used to drive an in-process ``PlanService``, so a plan
# handed to PFactory over MCP was built inside the MCP subprocess and never
# reached the server: invisible to the portal, invisible to
# ``GET /api/plan/sessions``, gone when the subprocess exited — while the tool
# returned a complete, plausible success payload. These routes give those tools
# somewhere real to write, on the same scope-gated proxy the task tools use.
#
# Unlike the task routes above, the plan handlers are imported at module level.
# ``main.py`` already imports ``plan_pipeline`` (and so builds the plan service)
# before it includes this router, so deferring here would buy nothing — and
# ``Annotated`` dependencies keep FastAPI's DI out of default arguments.
# =============================================================================

_Read = Annotated[AuthenticatedKey | _LegacyAdminKey, Depends(require_acw_scope(MCP_READ_SCOPE))]
_PlanWrite = Annotated[
    AuthenticatedKey | _LegacyAdminKey, Depends(require_acw_scope(PLAN_WRITE_SCOPE))
]


class _ApprovedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    path: str = Field(max_length=512)
    content: str = Field(max_length=_MAX_ARTIFACT_BYTES)
    blob_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    approve_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    approve_commit_author: str = Field(max_length=200)

    @field_validator("stage")
    @classmethod
    def valid_stage(cls, value: str) -> str:
        if value not in {"intent", "spec", "plan"}:
            raise ValueError("invalid stage")
        return value

    @field_validator("content")
    @classmethod
    def valid_content(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError("artifact content exceeds 256 KiB")
        return value

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not value or value.startswith("/") or ".." in value.split("/") or "\\" in value:
            raise ValueError("path must be repo-relative without '..'")
        return value

    @field_validator("approve_commit_author")
    @classmethod
    def valid_author(cls, value: str) -> str:
        if not value or not value.isprintable():
            raise ValueError("author must be printable")
        return value


class _IngestApprovedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    provider: str = Field(pattern=r"^[a-z_]{1,32}$")
    repo: str = Field(min_length=1, max_length=256)
    base_ref: str = Field(min_length=1, max_length=255)
    branch: str = Field(min_length=1, max_length=255)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    project_id: str | None = None
    artifacts: list[_ApprovedArtifact] = Field(min_length=3, max_length=3)

    @field_validator("artifacts")
    @classmethod
    def valid_artifacts(cls, value: list[_ApprovedArtifact]) -> list[_ApprovedArtifact]:
        if {item.stage for item in value} != {"intent", "spec", "plan"}:
            raise ValueError("exactly one artifact per stage is required")
        return value

    @field_validator("base_ref", "branch")
    @classmethod
    def valid_ref(cls, value: str) -> str:
        import re  # noqa: PLC0415

        if (
            value == "@"
            or value.startswith(("-", "/"))
            or value.endswith(("/", "."))
            or "//" in value
            or ".." in value
            or "@{" in value
            or any(part.endswith(".lock") or part.startswith(".") for part in value.split("/"))
            or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
        ):
            raise ValueError("invalid git ref")
        return value

    @model_validator(mode="after")
    def valid_paths(self) -> _IngestApprovedBody:
        if not self.slug or any(
            item.path != f"{item.stage}/{self.slug}.md" for item in self.artifacts
        ):
            raise ValueError("artifact paths must match the slug")
        return self


@router.get("/plan/sessions")
async def proxy_list_plan_sessions(
    request: FastAPIRequest, key: _Read, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("viewer").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org rule needs the principal.
    request.state.user = principal
    return await plan_list_sessions(request, db=db)


@router.get("/plan/sessions/{session_id}")
async def proxy_get_plan_session(
    session_id: str,
    request: FastAPIRequest,
    key: _Read,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("viewer").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org rule needs the principal.
    request.state.user = principal
    return await plan_get_session(session_id, request, db=db)


@router.get("/plan/sessions/{session_id}/audit-pack")
async def proxy_plan_audit_pack(
    session_id: str,
    request: FastAPIRequest,
    key: _Read,
    db: Annotated[AsyncSession, Depends(get_db)],
    # Aliased because the REST parameter is `format`, which shadows a builtin.
    fmt: Annotated[str, Query(alias="format")] = "json",
) -> Any:
    principal = _principal(key)
    await PlanAccessChecker("viewer").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org rule needs the principal.
    request.state.user = principal
    return await plan_audit_pack(session_id, request, format=fmt, db=db)


@router.get("/plan/meta/categories")
async def proxy_plan_categories(_: _Read) -> dict[str, Any]:
    return await plan_categories()


@router.post("/plan/sessions/ingest-text")
async def proxy_plan_ingest_text(
    request: FastAPIRequest, key: _PlanWrite, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/: the ingest org rule needs the principal.
    request.state.user = principal
    body = await _read_json_body(request)
    try:
        parsed = IngestTextBody(**body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    result = await plan_ingest_text(parsed, request, db=db)
    session_id = result.get("session_id") if isinstance(result, dict) else None
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_INGEST,
        "plan_session",
        session_id,
        request,
        details={"title": body.get("title")},
    )
    return result


@router.post("/plan/sessions/ingest-approved")
async def proxy_plan_ingest_approved(
    request: FastAPIRequest, key: _PlanWrite, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    request.state.user = principal
    try:
        body = _IngestApprovedBody(**(await _read_json_body(request)))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    access = await require_repo_project_role(
        principal,
        body.repo,
        "member",
        db,
        provider=body.provider,
        project_id=body.project_id,
    )
    for item in body.artifacts:
        raw = item.content.encode("utf-8")
        digest = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw,
            usedforsecurity=False,
        ).hexdigest()
        if digest != item.blob_sha:
            raise HTTPException(status_code=422, detail=f"{item.stage} blob SHA mismatch")
    try:
        session, created = await SERVICE.ingest_approved_async(
            [item.model_dump() for item in body.artifacts],
            provider=body.provider,
            repo=body.repo,
            base_ref=body.base_ref,
            project_id=access.project_id,
            tenant_id=resolve_tenant(request),
            org_id=access.org_id,
            **_attribution(principal),
        )
    except ArtifactIngestError as exc:
        raise HTTPException(status_code=422, detail=client_error(exc)) from exc
    except (EmitInProgressError, PlanServiceError) as exc:
        raise HTTPException(status_code=409, detail=client_error(exc)) from exc
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_INGEST,
        "plan_session",
        session.session_id,
        request,
        details={"slug": body.slug},
    )
    readiness = session.review.readiness if session.review else None
    return {
        "session_id": session.session_id,
        "correlation_key": session.correlation_key,
        "status": session.status,
        "deduped": not created,
        "children": [
            {"key": child.key, "title": child.title, "kind": child.kind}
            for child in session.epic.children
        ]
        if session.epic
        else [],
        "readiness": {
            "hard_failures": [
                check.check_id for check in readiness.unwaived_hard_failures(session.plan)
            ]
            if readiness
            else []
        },
    }


@router.post("/plan/sessions/ingest")
async def proxy_plan_ingest_upload(  # noqa: PLR0913 - one param per multipart form field
    request: FastAPIRequest,
    key: _PlanWrite,
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    title: Annotated[str | None, Form()] = None,
    category: Annotated[str, Form()] = "",
    template: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """Multipart ingest, so ``plan_ingest(path=...)`` keeps working for pdf/docx.

    The MCP subprocess runs on the caller's machine; the server cannot read that
    filesystem. The tool reads the file locally and uploads the bytes here, so
    text extraction stays server-side where the parsers live.
    """
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/: the ingest org rule needs the principal.
    request.state.user = principal
    result = await plan_ingest_upload(
        request,
        file=file,
        title=title,
        category=category,
        template=template,
        # Called directly, so FastAPI does not fill the Form() defaults.
        repo=None,
        base_ref=None,
        db=db,
    )
    session_id = result.get("session_id") if isinstance(result, dict) else None
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_INGEST,
        "plan_session",
        session_id,
        request,
        details={"filename": file.filename},
    )
    return result


@router.post("/plan/sessions/{session_id}/process")
async def proxy_plan_process(
    session_id: str,
    request: FastAPIRequest,
    key: _PlanWrite,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org rule needs the principal.
    request.state.user = principal
    result = await plan_process(session_id, request, db=db)
    await _audit_mcp_write(key, ACTION_MCP_PLAN_PROCESS, "plan_session", session_id, request)
    return result


@router.post("/plan/sessions/{session_id}/approve")
async def proxy_plan_approve(
    session_id: str,
    request: FastAPIRequest,
    key: _PlanWrite,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org rule needs the principal.
    request.state.user = principal
    body = await _read_json_body(request)
    try:
        parsed = ApproveBody(**body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    result = await plan_approve(session_id, parsed, request, db=db)
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_APPROVE,
        "plan_session",
        session_id,
        request,
        details={"approver": body.get("approver")},
    )
    return result


@router.post("/plan/sessions/{session_id}/reject")
async def proxy_plan_reject(
    session_id: str,
    request: FastAPIRequest,
    key: _PlanWrite,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    # AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org rule needs the principal.
    request.state.user = principal
    body = await _read_json_body(request)
    try:
        parsed = RejectBody(**body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_input=False, include_context=False, include_url=False),
        ) from exc
    result = await plan_reject(session_id, parsed, request, db=db)
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_REJECT,
        "plan_session",
        session_id,
        request,
        details={"approver": body.get("approver")},
    )
    return result


@router.post("/plan/sessions/{session_id}/emit-contract")
async def proxy_plan_emit_contract(
    session_id: str,
    request: FastAPIRequest,
    key: _PlanWrite,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    principal = _principal(key)
    await PlanAccessChecker("member").check(principal, request.url.path, db)
    request.state.user = principal
    body = EmitContractBody(**(await _read_json_body(request)))
    result = await plan_emit_contract(session_id, body, request, db=db, require_user_approval=True)
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_EMIT,
        "plan_session",
        session_id,
        request,
        details={"dry_run": body.dry_run},
    )
    return result
