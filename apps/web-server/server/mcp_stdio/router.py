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

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request as FastAPIRequest, UploadFile

from server.routes.plan_meta import categories as plan_categories
from server.routes.plan_pipeline import (
    ApproveBody,
    IngestTextBody,
    approve as plan_approve,
    audit_pack as plan_audit_pack,
    get_session as plan_get_session,
    ingest_text as plan_ingest_text,
    ingest_upload as plan_ingest_upload,
    list_sessions as plan_list_sessions,
    process as plan_process,
)
from server.services.audit_service import (
    ACTION_MCP_PLAN_APPROVE,
    ACTION_MCP_PLAN_INGEST,
    ACTION_MCP_PLAN_PROCESS,
)

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


async def _read_json_body(request: FastAPIRequest) -> dict:
    """Read request body, return {} if empty."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
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
async def proxy_list_projects(_=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from ..routes.projects import list_projects

    return await list_projects()


@router.get("/tasks")
async def proxy_list_tasks(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    from ..routes.tasks import list_tasks

    return await list_tasks(project_id=project_id, status=status)


@router.get("/tasks/running")
async def proxy_get_running_tasks(_=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from ..routes.execution import get_running_tasks

    return await get_running_tasks()


@router.get("/tasks/{task_id}")
async def proxy_get_task(task_id: str, _=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from ..routes.tasks import get_task

    return await get_task(task_id)


@router.get("/tasks/{task_id}/status")
async def proxy_get_task_status(task_id: str, _=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from ..routes.execution import get_task_status

    return await get_task_status(task_id)


@router.get("/tasks/{task_id}/logs")
async def proxy_get_task_logs(
    task_id: str,
    tail: int = Query(default=100),
    _=Depends(require_acw_scope(MCP_READ_SCOPE)),
):
    from ..routes.tasks import get_task_logs

    return await get_task_logs(task_id, tail=tail)


@router.get("/tasks/{task_id}/worktree/diff")
async def proxy_get_worktree_diff(task_id: str, _=Depends(require_acw_scope(MCP_READ_SCOPE))):
    from ..routes.tasks import get_worktree_diff

    return await get_worktree_diff(task_id)


# =============================================================================
# Project mutation — project:write
# =============================================================================


@router.post("/projects", status_code=201)
async def proxy_add_project(
    request: FastAPIRequest,
    key=Depends(require_acw_scope(PROJECT_WRITE_SCOPE)),
):
    from ..routes.projects import ProjectCreate, add_project

    body = await _read_json_body(request)
    result = await add_project(ProjectCreate(**body))
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
async def proxy_create_and_run_task(
    request: FastAPIRequest,
    project_id: str = Query(...),
    title: str = Query(...),
    description: str = Query(...),
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
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
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
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
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
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
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
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
    key=Depends(require_acw_scope(TASK_WRITE_SCOPE)),
):
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
    key=Depends(require_acw_scope(TASK_MERGE_SCOPE)),
):
    from ..routes.tasks import CreatePRFromTaskOptions, create_pr_from_task

    body = await _read_json_body(request)
    result = await create_pr_from_task(task_id, CreatePRFromTaskOptions(**body) if body else None)
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
    key=Depends(require_acw_scope(TASK_MERGE_SCOPE)),
):
    from ..routes.tasks import WorktreeMergeOptions, merge_worktree

    body = await _read_json_body(request)
    result = await merge_worktree(task_id, WorktreeMergeOptions(**body) if body else None)
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


@router.get("/plan/sessions")
async def proxy_list_plan_sessions(request: FastAPIRequest, _: _Read) -> dict[str, Any]:
    return await plan_list_sessions(request)


@router.get("/plan/sessions/{session_id}")
async def proxy_get_plan_session(
    session_id: str, request: FastAPIRequest, _: _Read
) -> dict[str, Any]:
    return await plan_get_session(session_id, request)


@router.get("/plan/sessions/{session_id}/audit-pack")
async def proxy_plan_audit_pack(
    session_id: str,
    _: _Read,
    # Aliased because the REST parameter is `format`, which shadows a builtin.
    fmt: Annotated[str, Query(alias="format")] = "json",
) -> Any:
    return await plan_audit_pack(session_id, format=fmt)


@router.get("/plan/meta/categories")
async def proxy_plan_categories(_: _Read) -> dict[str, Any]:
    return await plan_categories()


@router.post("/plan/sessions/ingest-text")
async def proxy_plan_ingest_text(request: FastAPIRequest, key: _PlanWrite) -> dict[str, Any]:
    body = await _read_json_body(request)
    result = await plan_ingest_text(IngestTextBody(**body), request)
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


@router.post("/plan/sessions/ingest")
async def proxy_plan_ingest_upload(  # noqa: PLR0913 - one param per multipart form field
    request: FastAPIRequest,
    key: _PlanWrite,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    category: Annotated[str, Form()] = "",
    template: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """Multipart ingest, so ``plan_ingest(path=...)`` keeps working for pdf/docx.

    The MCP subprocess runs on the caller's machine; the server cannot read that
    filesystem. The tool reads the file locally and uploads the bytes here, so
    text extraction stays server-side where the parsers live.
    """
    result = await plan_ingest_upload(
        request, file=file, title=title, category=category, template=template
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
    session_id: str, request: FastAPIRequest, key: _PlanWrite
) -> dict[str, Any]:
    result = await plan_process(session_id, request)
    await _audit_mcp_write(key, ACTION_MCP_PLAN_PROCESS, "plan_session", session_id, request)
    return result


@router.post("/plan/sessions/{session_id}/approve")
async def proxy_plan_approve(
    session_id: str, request: FastAPIRequest, key: _PlanWrite
) -> dict[str, Any]:
    body = await _read_json_body(request)
    result = await plan_approve(session_id, ApproveBody(**body))
    await _audit_mcp_write(
        key,
        ACTION_MCP_PLAN_APPROVE,
        "plan_session",
        session_id,
        request,
        details={"approver": body.get("approver")},
    )
    return result
