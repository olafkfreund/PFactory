"""
Global Events WebSocket with per-client routing.

Supports both broadcast (legacy) and targeted delivery based on
user identity.  When a JWT-authenticated user connects, events
can be routed only to members of the relevant organization.
Legacy (bearer-token) connections receive all events (backward
compatible).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import WebSocketAuthError, authenticate_websocket, verify_websocket_token

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Client tracking
# ---------------------------------------------------------------------------


@dataclass
class ConnectedClient:
    """A connected WebSocket client with optional identity."""

    websocket: WebSocket
    user_id: str | None = None
    org_ids: set[str] = field(default_factory=set)


# Active WebSocket connections — keyed by WebSocket object for fast lookup
_clients: dict[WebSocket, ConnectedClient] = {}

# Legacy set kept for backward compatibility with code that still
# references ``active_connections`` directly.
active_connections: set[WebSocket] = set()


def _register_client(ws: WebSocket, user_info: dict | None) -> ConnectedClient:
    """Register a new client connection."""
    client = ConnectedClient(
        websocket=ws,
        user_id=user_info["id"] if user_info else None,
    )
    _clients[ws] = client
    active_connections.add(ws)
    return client


def _unregister_client(ws: WebSocket) -> None:
    """Remove a client connection."""
    _clients.pop(ws, None)
    active_connections.discard(ws)


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------


async def broadcast_event(event_type: str, payload: dict):
    """Broadcast an event to all connected clients (legacy behavior)."""
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []

    for ws in list(active_connections):
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        _unregister_client(ws)


async def send_to_user(user_id: str, event_type: str, payload: dict):
    """Send an event to a specific user (all their connections)."""
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []

    for ws, client in list(_clients.items()):
        if client.user_id == user_id:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

    for ws in disconnected:
        _unregister_client(ws)


async def send_to_org(org_id: str, event_type: str, payload: dict):
    """Send an event only to members of a specific organization.

    Falls back to broadcast for legacy (non-JWT) connections so they
    aren't excluded.
    """
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []

    for ws, client in list(_clients.items()):
        # Send to: org members, or legacy clients (no user_id)
        if client.user_id is None or org_id in client.org_ids:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

    for ws in disconnected:
        _unregister_client(ws)


def update_client_orgs(user_id: str, org_ids: set[str]) -> None:
    """Update the org memberships for all connections of a given user.

    Call this after the user's org memberships change so routing
    reflects the new state.
    """
    for client in _clients.values():
        if client.user_id == user_id:
            client.org_ids = org_ids


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/events")
async def events_websocket(websocket: WebSocket):
    """WebSocket endpoint for global events."""
    await websocket.accept()

    # Authenticate — get user info if JWT, None for legacy token
    try:
        user_info = await authenticate_websocket(websocket)
    except WebSocketAuthError:
        return

    client = _register_client(websocket, user_info)

    # If authenticated user, load their org memberships for routing
    if user_info and user_info.get("id"):
        try:
            from ..database.engine import async_session_factory
            from ..database import OrgMember
            from sqlalchemy import select

            async with async_session_factory() as session:
                result = await session.execute(
                    select(OrgMember.org_id).where(
                        OrgMember.user_id == user_info["id"]
                    )
                )
                client.org_ids = {row[0] for row in result.all()}
        except Exception:
            logger.debug("Could not load org memberships for WS client", exc_info=True)

    try:
        # Keep connection alive and listen for pings
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)

                # Handle ping/pong
                if data == "ping":
                    await websocket.send_text("pong")

            except asyncio.TimeoutError:
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _unregister_client(websocket)


# Helper functions for different event types
async def emit_task_progress(task_id: str, progress: dict):
    import logging
    logging.getLogger(__name__).info(f"[WebSocket] Emitting task:progress - taskId: {task_id}, percentage: {progress.get('percentage', 'N/A')}%")
    await broadcast_event("task:progress", {"taskId": task_id, **progress})


async def emit_task_error(task_id: str, error: str):
    import logging
    logging.getLogger(__name__).info(f"[WebSocket] Emitting task:error - taskId: {task_id}, error: {error[:100]}...")
    await broadcast_event("task:error", {"taskId": task_id, "error": error})


async def emit_task_status(task_id: str, status: str, review_reason: str | None = None):
    import logging
    payload = {"taskId": task_id, "status": status}
    if review_reason:
        payload["reviewReason"] = review_reason
        logging.getLogger(__name__).info(f"[WebSocket] Emitting task:status - taskId: {task_id}, status: {status}, reviewReason: {review_reason}")
    else:
        logging.getLogger(__name__).info(f"[WebSocket] Emitting task:status - taskId: {task_id}, status: {status}")
    await broadcast_event("task:status", payload)


async def emit_task_log(task_id: str, log: str):
    import logging
    # Only log the first 50 chars to avoid flooding logs with full log content
    log_preview = log[:50].replace('\n', '\\n') if len(log) > 50 else log.replace('\n', '\\n')
    logging.getLogger(__name__).debug(f"[WebSocket] Emitting task:log - taskId: {task_id}, log: {log_preview}...")
    await broadcast_event("task:log", {"taskId": task_id, "log": log})


async def emit_task_update(task_id: str, task_data: dict):
    """Emit task data update for frontend to refresh task card."""
    import logging
    exec_progress = task_data.get("executionProgress", {})
    phase = exec_progress.get("phase", "N/A") if exec_progress else "N/A"
    progress = exec_progress.get("phaseProgress", "N/A") if exec_progress else "N/A"
    logging.getLogger(__name__).info(f"[WebSocket] Emitting task:update - taskId: {task_id}, phase: {phase}, progress: {progress}%")
    await broadcast_event("task:update", {"taskId": task_id, **task_data})


async def emit_changelog_progress(project_id: str, progress: dict):
    await broadcast_event("changelog:progress", {"projectId": project_id, **progress})


async def emit_insights_chunk(project_id: str, chunk: str):
    await broadcast_event("insights:chunk", {"projectId": project_id, "chunk": chunk})


async def emit_insights_status(project_id: str, status: str):
    await broadcast_event("insights:status", {"projectId": project_id, "status": status})


async def emit_profile_switch(task_id: str, switch_data: dict):
    """Emit profile switch event for reactive failover."""
    import logging
    from_profile = switch_data.get("fromProfile", "N/A")
    to_profile = switch_data.get("toProfile", "N/A")
    logging.getLogger(__name__).info(f"[WebSocket] Emitting task:profile-switch - taskId: {task_id}, from: {from_profile}, to: {to_profile}")
    await broadcast_event("task:profile-switch", {"taskId": task_id, **switch_data})


async def emit_task_logs_stream(spec_id: str, chunk: dict):
    """Emit a task log chunk for real-time streaming to open task detail modals.

    This event streams individual log entries as they're added to task_logs.json,
    enabling live updates in the frontend without file polling.

    Args:
        spec_id: The spec/task identifier (e.g., "007-task-update-progress-logs")
        chunk: The log chunk dict matching TaskLogStreamChunk interface:
            - type: 'text' | 'tool_start' | 'tool_end' | 'phase_start' | 'phase_end' | 'error'
            - content: (optional) Log message content
            - phase: (optional) Current phase (planning, coding, validation)
            - timestamp: (optional) ISO timestamp
            - tool: (optional) { name: string, input?: string, success?: boolean }
            - subtask_id: (optional) Current subtask identifier
    """
    import logging
    chunk_type = chunk.get("type", "unknown")
    content_preview = chunk.get("content", "")[:50].replace('\n', '\\n') if chunk.get("content") else ""
    logging.getLogger(__name__).debug(
        f"[WebSocket] Emitting task-logs:stream - specId: {spec_id}, "
        f"type: {chunk_type}, content: {content_preview}..."
    )
    await broadcast_event("task-logs:stream", {"specId": spec_id, "chunk": chunk})


async def emit_subtask_update(task_id: str, subtask_id: str, status: str, previous_status: str | None = None):
    """Emit a subtask status change event for granular real-time updates.

    This event is emitted when an individual subtask's status changes, allowing
    the frontend to update subtask checkmarks in real-time without waiting for
    the full task update cycle.

    Args:
        task_id: The task/spec identifier
        subtask_id: The subtask identifier (e.g., "1.1", "2.3")
        status: The new status ("pending", "in_progress", "completed", "failed")
        previous_status: The previous status (optional, for logging/debugging)
    """
    import logging
    logger = logging.getLogger(__name__)
    if previous_status:
        logger.info(
            f"[WebSocket] Emitting task:subtask-update - taskId: {task_id}, "
            f"subtaskId: {subtask_id}, status: {previous_status} -> {status}"
        )
    else:
        logger.info(
            f"[WebSocket] Emitting task:subtask-update - taskId: {task_id}, "
            f"subtaskId: {subtask_id}, status: {status}"
        )
    await broadcast_event("task:subtask-update", {
        "taskId": task_id,
        "subtaskId": subtask_id,
        "status": status,
        "previousStatus": previous_status,
    })
