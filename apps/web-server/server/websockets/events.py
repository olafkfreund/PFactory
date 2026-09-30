"""
Global Events WebSocket with per-client routing.

Supports both broadcast (legacy) and targeted delivery based on
user identity.  When a JWT-authenticated user connects, events
can be routed only to members of the relevant organization.
Legacy (bearer-token) connections receive all events (backward
compatible).
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from factory_common.logsafe import sanitize_log

from ..auth import WebSocketAuthError, authenticate_websocket
from . import relay

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

# How long a single client's send may take before it's treated as gone
# (#804 finding 5). Without this, one wedged client (a browser that stopped
# reading) blocks `ws.send_text` indefinitely, which — since the three
# `_deliver_local_*` loops below send to every client from a single task —
# stalls delivery to every OTHER client too; with the relay now waiting on
# this loop before it can even queue a notification for other pods, one
# wedged local client turns into a pod-wide outage. 5 seconds: long enough
# that a slow-but-alive connection (a mobile client with real latency or a
# stalled-but-recovering TCP window) survives, short enough that a genuinely
# dead one can't hold up delivery for more than a few seconds at a time.
_SEND_TIMEOUT = 5.0

# How long `events_websocket`'s own loop waits for a client message before
# sending a keepalive ping. A named constant (not a bare `30` inline) so
# tests can bound it down instead of actually waiting 30s.
_RECEIVE_POLL_INTERVAL = 30.0


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
    _slow_client_state.pop(ws, None)


async def _evict_client(ws: WebSocket) -> None:
    """Unregister a client AND close its socket (#804 finding 3 FINAL call).

    Reserved for a GENUINE send failure, not a timeout (see `_send_or_skip`).
    `_unregister_client` alone leaves the actual connection open: the
    browser still looks connected, and `events_websocket`'s own 30s
    keepalive loop keeps writing to it, but the client is no longer in
    `_clients`/`active_connections` so it will never again receive anything
    through `_deliver_local_*` — a silent blackhole. Closing here causes
    `events_websocket`'s `receive_text()` (running in ITS OWN task) to raise
    `WebSocketDisconnect`, which it already handles via its existing
    `except WebSocketDisconnect: pass` + `finally: _unregister_client(...)`
    (a harmless, idempotent second unregister) — so the browser's own
    reconnect logic kicks in instead of talking to a phantom socket forever.
    """
    with contextlib.suppress(Exception):
        # Bounded (#804 round 6 finding B): this usually runs on a socket
        # that JUST raised from `send_text`, but a half-open peer can hang
        # `close()` too, and it runs SEQUENTIALLY, after `gather`, for every
        # evicted client in the batch — one hung `close()` would otherwise
        # stall eviction of every other client behind it in that list.
        # `asyncio.wait_for`'s own `TimeoutError` is an `Exception` subclass,
        # already caught by this `suppress`.
        await asyncio.wait_for(ws.close(), timeout=_SEND_TIMEOUT)
    _unregister_client(ws)


@dataclass
class _SlowClientState:
    """Per-client timeout tracking (#804 finding 3/4).

    KEYED BY WEBSOCKET, not a single module-level flag: under `gather`, a
    healthy client's SUCCESS and a wedged client's TIMEOUT land in the same
    broadcast call, and a shared flag conflates them — a reproduction
    confirmed 1 wedged + 1 healthy client, 3 broadcasts, logged 3 "send timed
    out" warnings instead of the claimed 1, because the healthy client's
    success reset the shared flag before the wedged one's own warning check
    ran. Per-client state makes "deduped" and "reset" both mean what they say
    for THAT client specifically.
    """

    consecutive_timeouts: int = 0
    warned: bool = False


# Cleaned up in `_unregister_client` — a client that's gone shouldn't leak an
# entry here forever.
_slow_client_state: dict[WebSocket, _SlowClientState] = {}

# How many CONSECUTIVE timeouts (no successful send in between) before a
# "slow" client is reclassified as dead and evicted (#804 finding 4).
# `_send_or_skip`'s skip-not-evict policy assumes a TRANSIENT stall that
# self-heals on the next event; a client that never drains instead sits
# registered forever, receiving nothing, while taxing every subsequent
# broadcast with a full `_SEND_TIMEOUT` of `gather` latency. 3, matching
# `relay.py`'s `_DRAIN_MAX_ATTEMPTS` reasoning: 3 consecutive 5s stalls with
# zero successful sends in between (15s of unbroken silence from a client
# that's supposedly still connected) is a dead client by any reasonable
# reading, not a bad TCP window.
_MAX_CONSECUTIVE_TIMEOUTS = 3


async def _send_or_skip(ws: WebSocket, message: str, disconnected: list[WebSocket]) -> None:
    """Send one message to one client; append to ``disconnected`` only on a
    genuine failure OR a client stuck timing out for `_MAX_CONSECUTIVE_TIMEOUTS`
    sends in a row.

    #804 finding 3: a `TimeoutError` (a slow-but-alive client — a full TCP
    window on a mobile link during a `task-logs:stream` burst, not a dead
    peer) is deliberately NOT treated as an immediate disconnect. Evicting on
    the FIRST timeout converts "slow" into "disconnected", and under the
    exact burst load that caused the timeout in the first place, that
    produces flapping: burst -> close -> reconnect -> burst -> close.
    Skipping loses one message and self-heals on the next event for a
    TRANSIENT stall. Finding 4: that reasoning stops holding for a client
    that never drains at all — see `_MAX_CONSECUTIVE_TIMEOUTS`. Any genuine
    send exception (not a timeout) still goes through `_evict_client`
    immediately, unchanged.
    """
    try:
        await asyncio.wait_for(ws.send_text(message), timeout=_SEND_TIMEOUT)
    except TimeoutError:
        state = _slow_client_state.setdefault(ws, _SlowClientState())
        state.consecutive_timeouts += 1
        if state.consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
            disconnected.append(ws)
            return
        if not state.warned:
            state.warned = True
            logger.warning("[events] client send timed out, skipping for this message")
        return
    except Exception:
        disconnected.append(ws)
        return
    _slow_client_state.pop(ws, None)


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------


async def _deliver_local_broadcast(event_type: str, payload: dict[str, Any]) -> None:
    """Send an event to every client connected to THIS pod (legacy behavior).

    Sends CONCURRENTLY (#804 round 5 finding 3), not one client at a time:
    finding 4 already took Postgres off the stdout path
    (`_emit_progress` → `broadcast_event` → here), but a sequential loop
    still costs N x `_SEND_TIMEOUT` inline on that same path when N clients
    are all slow at once. Each client gets its own message and its own
    independent outcome, so there is no ordering requirement between them.
    """
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []

    await asyncio.gather(
        *(_send_or_skip(ws, message, disconnected) for ws in list(active_connections)),
        return_exceptions=True,
    )

    for ws in disconnected:
        await _evict_client(ws)


async def broadcast_event(event_type: str, payload: dict):
    """Broadcast an event to all connected clients (legacy behavior).

    Delivers locally first — unconditionally, so a relay hiccup never
    silences a browser on this pod — then relays to other pods over
    Postgres so their own locally-connected clients get it too (#804).
    """
    await _deliver_local_broadcast(event_type, payload)
    await relay.publish("ws:broadcast", {"event_type": event_type, "payload": payload})


async def _deliver_local_to_user(user_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Send an event to a specific user's connections on THIS pod.

    Sends CONCURRENTLY — see `_deliver_local_broadcast` for why.
    """
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []
    targets = [ws for ws, client in list(_clients.items()) if client.user_id == user_id]

    await asyncio.gather(
        *(_send_or_skip(ws, message, disconnected) for ws in targets), return_exceptions=True
    )

    for ws in disconnected:
        await _evict_client(ws)


async def send_to_user(user_id: str, event_type: str, payload: dict):
    """Send an event to a specific user (all their connections).

    Delivers locally first, then relays to other pods (#804) — the user's
    other connections may be on a different pod.
    """
    await _deliver_local_to_user(user_id, event_type, payload)
    await relay.publish(
        "ws:user", {"user_id": user_id, "event_type": event_type, "payload": payload}
    )


async def _deliver_local_to_org(org_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Send an event to members of an org connected to THIS pod.

    Falls back to broadcast for legacy (non-JWT) connections so they
    aren't excluded. Sends CONCURRENTLY — see `_deliver_local_broadcast` for
    why.
    """
    message = json.dumps({"type": event_type, "payload": payload})
    disconnected: list[WebSocket] = []
    # Send to: org members, or legacy clients (no user_id)
    targets = [
        ws
        for ws, client in list(_clients.items())
        if client.user_id is None or org_id in client.org_ids
    ]

    await asyncio.gather(
        *(_send_or_skip(ws, message, disconnected) for ws in targets), return_exceptions=True
    )

    for ws in disconnected:
        await _evict_client(ws)


async def send_to_org(org_id: str, event_type: str, payload: dict):
    """Send an event only to members of a specific organization.

    Delivers locally first, then relays to other pods (#804) — org
    members may be connected to a different pod.
    """
    await _deliver_local_to_org(org_id, event_type, payload)
    await relay.publish("ws:org", {"org_id": org_id, "event_type": event_type, "payload": payload})


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
            from sqlalchemy import select

            from ..database import OrgMember
            from ..database.engine import async_session_factory

            async with async_session_factory() as session:
                result = await session.execute(
                    select(OrgMember.org_id).where(OrgMember.user_id == user_info["id"])
                )
                client.org_ids = {row[0] for row in result.all()}
        except Exception:
            logger.debug("Could not load org memberships for WS client", exc_info=True)

    try:
        # Keep connection alive and listen for pings
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=_RECEIVE_POLL_INTERVAL
                )

                # Handle ping/pong
                if data == "ping":
                    await websocket.send_text("pong")

            except TimeoutError:
                try:
                    # Bounded (#804 round 6 finding B): a bare `send_text`
                    # here blocks THIS task forever on a wedged socket, which
                    # never reaches `finally: _unregister_client` — so even
                    # with consecutive-timeout eviction on the DELIVERY path
                    # (`_send_or_skip`), this endpoint's own task stays
                    # parked until the OS eventually tears the connection
                    # down, keeping the client "registered but unreachable"
                    # far longer than the delivery path's own eviction would
                    # suggest.
                    await asyncio.wait_for(
                        websocket.send_text(json.dumps({"type": "ping"})), timeout=_SEND_TIMEOUT
                    )
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

    logging.getLogger(__name__).info(
        "[WebSocket] Emitting task:progress - taskId: %s, percentage: %s%%",
        sanitize_log(task_id),
        sanitize_log(progress.get("percentage", "N/A")),
    )
    await broadcast_event("task:progress", {"taskId": task_id, **progress})


async def emit_task_error(task_id: str, error: str):
    import logging

    logging.getLogger(__name__).info(
        "[WebSocket] Emitting task:error - taskId: %s, error: %s...",
        sanitize_log(task_id),
        sanitize_log(error[:100]),
    )
    await broadcast_event("task:error", {"taskId": task_id, "error": error})


async def emit_task_status(task_id: str, status: str, review_reason: str | None = None):
    import logging

    payload = {"taskId": task_id, "status": status}
    if review_reason:
        payload["reviewReason"] = review_reason
        logging.getLogger(__name__).info(
            "[WebSocket] Emitting task:status - taskId: %s, status: %s, reviewReason: %s",
            sanitize_log(task_id),
            sanitize_log(status),
            sanitize_log(review_reason),
        )
    else:
        logging.getLogger(__name__).info(
            "[WebSocket] Emitting task:status - taskId: %s, status: %s",
            sanitize_log(task_id),
            sanitize_log(status),
        )
    await broadcast_event("task:status", payload)


async def emit_task_log(task_id: str, log: str):
    import logging

    # Only log the first 50 chars to avoid flooding logs with full log content
    log_preview = log[:50].replace("\n", "\\n") if len(log) > 50 else log.replace("\n", "\\n")
    logging.getLogger(__name__).debug(
        "[WebSocket] Emitting task:log - taskId: %s, log: %s...",
        sanitize_log(task_id),
        sanitize_log(log_preview),
    )
    await broadcast_event("task:log", {"taskId": task_id, "log": log})


async def emit_task_update(task_id: str, task_data: dict):
    """Emit task data update for frontend to refresh task card."""
    import logging

    exec_progress = task_data.get("executionProgress", {})
    phase = exec_progress.get("phase", "N/A") if exec_progress else "N/A"
    progress = exec_progress.get("phaseProgress", "N/A") if exec_progress else "N/A"
    logging.getLogger(__name__).info(
        "[WebSocket] Emitting task:update - taskId: %s, phase: %s, progress: %s%%",
        sanitize_log(task_id),
        sanitize_log(phase),
        sanitize_log(progress),
    )
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
    logging.getLogger(__name__).info(
        "[WebSocket] Emitting task:profile-switch - taskId: %s, from: %s, to: %s",
        sanitize_log(task_id),
        sanitize_log(from_profile),
        sanitize_log(to_profile),
    )
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
    content_preview = (
        chunk.get("content", "")[:50].replace("\n", "\\n") if chunk.get("content") else ""
    )
    logging.getLogger(__name__).debug(
        "[WebSocket] Emitting task-logs:stream - specId: %s, type: %s, content: %s...",
        sanitize_log(spec_id),
        sanitize_log(chunk_type),
        sanitize_log(content_preview),
    )
    await broadcast_event("task-logs:stream", {"specId": spec_id, "chunk": chunk})


async def emit_subtask_update(
    task_id: str, subtask_id: str, status: str, previous_status: str | None = None
):
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
            "[WebSocket] Emitting task:subtask-update - taskId: %s, subtaskId: %s, status: %s -> %s",
            sanitize_log(task_id),
            sanitize_log(subtask_id),
            sanitize_log(previous_status),
            sanitize_log(status),
        )
    else:
        logger.info(
            "[WebSocket] Emitting task:subtask-update - taskId: %s, subtaskId: %s, status: %s",
            sanitize_log(task_id),
            sanitize_log(subtask_id),
            sanitize_log(status),
        )
    await broadcast_event(
        "task:subtask-update",
        {
            "taskId": task_id,
            "subtaskId": subtask_id,
            "status": status,
            "previousStatus": previous_status,
        },
    )
