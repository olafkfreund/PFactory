"""
Insights chat routes.

AI-powered insights chat: session CRUD (list/new/switch/delete/rename),
model config, message send/stop, task generation, and session clear.

Two routers, mounted at distinct prefixes by ``routes/projects.py``:
- ``router``       → ``/{projectId}/insights``
- ``files_router`` → ``/{projectId}/files/insights`` (the files-panel clear alias)
"""

import logging
from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from factory_common.logsafe import sanitize_log

from ..services.insights_service import get_insights_service

logger = logging.getLogger(__name__)

router = APIRouter()
files_router = APIRouter()


def _get_project_path(project_id: str) -> FilePath:
    """Get project path from project ID (delegates to the canonical resolver)."""
    # Import here to avoid circular import.
    from .projects import resolve_project_path

    return resolve_project_path(project_id)


# ============================================
# Request/Response Models
# ============================================


class InsightsMessageRequest(BaseModel):
    message: str
    modelConfig: dict | None = None


class GenerateTaskRequest(BaseModel):
    modelConfig: dict | None = None


class CreateTaskRequest(BaseModel):
    title: str
    description: str
    metadata: dict | None = None


class RenameSessionRequest(BaseModel):
    title: str


class UpdateModelConfigRequest(BaseModel):
    provider: str | None = None
    profileId: str | None = None
    model: str | None = None
    thinkingLevel: str | None = None
    temperature: float | None = None


# ============================================
# Insights Routes
# ============================================


@router.get("/providers")
async def detect_insights_providers(projectId: str = Path(...)):
    """Detect all available LLM providers for insights chat."""
    from ..services.insights_providers import detect_all_providers

    providers = await detect_all_providers()
    return {
        "success": True,
        "data": [p.to_dict() for p in providers],
    }


@router.get("")
async def get_insights_session(projectId: str = Path(...)):
    """Get current insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    session = service.get_current_session(project_path, projectId)

    return {
        "success": True,
        "data": {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "suggestedTask": msg.suggested_task,
                    "toolsUsed": msg.tools_used,
                }
                for msg in session.messages
            ],
            "modelConfig": session.model_config,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        },
    }


@router.post("/message")
async def send_insights_message(projectId: str = Path(...), request: InsightsMessageRequest = ...):
    """Send a message to insights AI."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()

    # Start message processing in background (non-blocking, tracked for cancellation)
    service.start_message(
        project_path=project_path,
        project_id=projectId,
        message=request.message,
        model_config=request.modelConfig,
    )

    return {"success": True}


@router.post("/stop")
async def stop_insights_message(projectId: str = Path(...)):
    """Stop the currently running insights chat for a project."""
    service = get_insights_service()
    cancelled = service.stop_message(projectId)
    return {"success": True, "cancelled": cancelled}


@router.delete("")
async def clear_insights_session(projectId: str = Path(...)):
    """
    Clear current insights session and create a new one.

    This endpoint:
    - Deletes the current active session (if it exists)
    - Creates a new empty session
    - Sets the new session as active
    - Returns the newly created session data

    Args:
        projectId: The project ID

    Returns:
        Success response with the newly created session data

    Raises:
        HTTPException: 404 if project not found
        HTTPException: 500 if session clearing fails
    """
    try:
        # Get project path (raises 404 if project not found)
        project_path = _get_project_path(projectId)

        # Get insights service
        service = get_insights_service()

        # Clear current session and create new one
        # This deletes the old session and returns the newly created session
        new_session = service.clear_session(project_path, projectId)

        # Return success with new session data (similar to new_insights_session endpoint)
        return {
            "success": True,
            "message": "Session cleared successfully",
            "data": {
                "id": new_session.id,
                "projectId": new_session.project_id,
                "title": new_session.title,
                "messageCount": len(new_session.messages),
                "createdAt": new_session.created_at,
                "updatedAt": new_session.updated_at,
            },
        }
    except HTTPException:
        # Re-raise HTTP exceptions (like 404 from _get_project_path)
        raise
    except Exception as e:
        # Log error and return 500
        logging.getLogger(__name__).error(
            "Failed to clear insights session: %s", sanitize_log(e), exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Failed to clear insights session: {e!s}")


@router.post("/create-task")
async def create_task_from_insights(projectId: str = Path(...), request: CreateTaskRequest = ...):
    """Create a task from insights conversation."""
    from .tasks import TaskCreate, create_task

    try:
        task_request = TaskCreate(
            project_id=projectId,
            title=request.title,
            description=request.description,
        )
        result = await create_task(task_request)
        return {"success": True, "data": result}
    except Exception:
        logger.exception(
            "create_task_from_insights failed for project_id=%s", sanitize_log(projectId)
        )
        return {"success": False, "error": "Failed to create task from insights."}


@router.post("/generate-task")
async def generate_task_from_chat(projectId: str = Path(...), request: GenerateTaskRequest = ...):
    """Generate a structured task (title + description) from the current chat session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()

    try:
        result = await service.generate_task_from_chat(
            project_path=project_path,
            project_id=projectId,
            model_config=request.modelConfig,
        )
        return {"success": True, "data": result}
    except Exception:
        logger.exception(
            "generate_task_from_chat failed for project_id=%s", sanitize_log(projectId)
        )
        return {"success": False, "error": "Failed to generate task from chat."}


@router.get("/sessions")
async def list_insights_sessions(projectId: str = Path(...)):
    """List all insights sessions."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    sessions = service.list_sessions(project_path)
    return {"success": True, "data": sessions}


@router.post("/sessions")
async def new_insights_session(projectId: str = Path(...)):
    """Create a new insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    session = service.create_session(project_path, projectId)

    return {
        "success": True,
        "data": {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [],
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        },
    }


@router.post("/sessions/{sessionId}/switch")
async def switch_insights_session(projectId: str = Path(...), sessionId: str = Path(...)):
    """Switch to a different insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    session = service.switch_session(project_path, sessionId)

    if not session:
        raise HTTPException(status_code=404, detail=f"Session {sessionId} not found")

    return {
        "success": True,
        "data": {
            "id": session.id,
            "projectId": session.project_id,
            "title": session.title,
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "suggestedTask": msg.suggested_task,
                    "toolsUsed": msg.tools_used,
                }
                for msg in session.messages
            ],
            "modelConfig": session.model_config,
            "createdAt": session.created_at,
            "updatedAt": session.updated_at,
        },
    }


@router.delete("/sessions/{sessionId}")
async def delete_insights_session(projectId: str = Path(...), sessionId: str = Path(...)):
    """Delete an insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    result = service.delete_session(project_path, sessionId)
    return {"success": result["deleted"], "data": {"switchedTo": result.get("switchedTo")}}


@router.patch("/sessions/{sessionId}")
async def rename_insights_session(
    projectId: str = Path(...), sessionId: str = Path(...), request: RenameSessionRequest = ...
):
    """Rename an insights session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    success = service.rename_session(project_path, sessionId, request.title)
    return {"success": success}


@router.patch("/sessions/{sessionId}/model")
async def update_insights_model_config(
    projectId: str = Path(...), sessionId: str = Path(...), request: UpdateModelConfigRequest = ...
):
    """Update model config for a session."""
    project_path = _get_project_path(projectId)
    service = get_insights_service()
    config = {k: v for k, v in request.model_dump().items() if v is not None}
    success = service.update_model_config(project_path, sessionId, config)
    return {"success": success}


# ============================================
# Files Insights Router (clear alias)
# ============================================


@files_router.delete("")
async def clear_files_insights_session(projectId: str):
    """
    Clear current files insights session and create a new one.

    This endpoint:
    - Deletes the current active session (if it exists)
    - Creates a new empty session
    - Sets the new session as active
    - Returns the newly created session data

    Args:
        projectId: The project ID

    Returns:
        Success response with the newly created session data

    Raises:
        HTTPException: 404 if project not found
        HTTPException: 500 if session clearing fails
    """
    try:
        # Get project path (raises 404 if project not found)
        project_path = _get_project_path(projectId)

        # Get insights service
        service = get_insights_service()

        # Clear current session and create new one
        # This deletes the old session and returns the newly created session
        new_session = service.clear_session(project_path, projectId)

        # Return success with new session data (similar to new_insights_session endpoint)
        return {
            "success": True,
            "message": "Files insights session cleared successfully",
            "data": {
                "id": new_session.id,
                "projectId": new_session.project_id,
                "title": new_session.title,
                "messageCount": len(new_session.messages),
                "createdAt": new_session.created_at,
                "updatedAt": new_session.updated_at,
            },
        }
    except HTTPException:
        # Re-raise HTTP exceptions (like 404 from _get_project_path)
        raise
    except Exception as e:
        # Log error and return 500
        logging.getLogger(__name__).error(
            "Failed to clear files insights session: %s", sanitize_log(e), exc_info=True
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to clear files insights session: {e!s}"
        )
