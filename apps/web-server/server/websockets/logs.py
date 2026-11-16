"""
WebSocket endpoint for streaming task logs.

Provides real-time log streaming from agent execution.
"""

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.services.agent_service import TaskLog, get_agent_service

from .task_access import AllTaskSocketAccess, TaskSocketAccess

router = APIRouter()
_REFRESH_SECONDS = 5.0


@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs_websocket(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task logs.

    Connect to receive real-time logs from a running task.
    Logs are sent as JSON objects with the following structure:
    {
        "task_id": "string",
        "content": "string",
        "timestamp": "ISO timestamp",
        "level": "info|warning|error|debug",
        "source": "agent|stdout|stderr"
    }
    """
    access = TaskSocketAccess(websocket, task_id)
    if not await access.connect():
        return

    await websocket.accept()

    agent_service = get_agent_service()
    message_queue: asyncio.Queue[TaskLog] = asyncio.Queue()

    async def log_callback(log: TaskLog):
        await message_queue.put(log)

    # Register callback
    unregister = agent_service.register_log_callback(task_id, log_callback)

    try:
        # Send initial connection message
        await websocket.send_json(
            {
                "type": "connected",
                "task_id": task_id,
                "message": "Connected to log stream",
            }
        )

        while True:
            if not await access.recheck():
                return
            try:
                # Wait for log with timeout to allow checking for disconnection
                log = await asyncio.wait_for(message_queue.get(), timeout=30.0)

                await websocket.send_json(
                    {
                        "type": "log",
                        "task_id": log.task_id,
                        "content": log.content,
                        "timestamp": log.timestamp,
                        "level": log.level,
                        "source": log.source,
                    }
                )

            except TimeoutError:
                # Send heartbeat to keep connection alive
                await websocket.send_json({"type": "heartbeat"})

    except WebSocketDisconnect:
        pass
    finally:
        unregister()


@router.websocket("/ws/logs")
async def all_logs_websocket(websocket: WebSocket):
    """WebSocket endpoint for streaming logs from ALL running tasks.

    Connect to receive real-time logs from all running tasks.
    Useful for a unified log view.
    """
    access = AllTaskSocketAccess(websocket)
    if not await access.connect():
        return

    await websocket.accept()

    agent_service = get_agent_service()
    message_queue: asyncio.Queue[TaskLog] = asyncio.Queue()
    # See progress.py: `callable` is the builtin function, not a type.
    unregister_callbacks: list[Callable[[], None]] = []
    allowed: set[str] = set()

    async def log_callback(log: TaskLog):
        await message_queue.put(log)

    async def refresh_subscriptions() -> bool:
        """Update subscriptions to match running tasks."""
        nonlocal allowed
        if not await access.recheck():
            return False
        # Unregister all current callbacks
        for unregister in unregister_callbacks:
            unregister()
        unregister_callbacks.clear()

        allowed = await access.readable(agent_service.get_running_tasks())
        for task_id in allowed:
            unregister = agent_service.register_log_callback(task_id, log_callback)
            unregister_callbacks.append(unregister)
        return True

    try:
        if not await refresh_subscriptions():
            return
        await websocket.send_json(
            {
                "type": "connected",
                "message": "Connected to unified log stream",
            }
        )

        loop = asyncio.get_running_loop()
        last_refresh = loop.time()

        while True:
            try:
                log = await asyncio.wait_for(
                    message_queue.get(),
                    timeout=max(0.0, _REFRESH_SECONDS - (loop.time() - last_refresh)),
                )
            except TimeoutError:
                log = None

            if loop.time() - last_refresh >= _REFRESH_SECONDS:
                if not await refresh_subscriptions():
                    return
                last_refresh = loop.time()
            if log is None:
                await websocket.send_json({"type": "heartbeat"})
            elif log.task_id in allowed:
                await websocket.send_json(
                    {
                        "type": "log",
                        "task_id": log.task_id,
                        "content": log.content,
                        "timestamp": log.timestamp,
                        "level": log.level,
                        "source": log.source,
                    }
                )

    except WebSocketDisconnect:
        pass
    finally:
        for unregister in unregister_callbacks:
            unregister()
