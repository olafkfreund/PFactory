"""
WebSocket endpoint for streaming task logs.

Provides real-time log streaming from agent execution.
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import verify_websocket_token
from ..services.agent_service import TaskLog, get_agent_service

router = APIRouter()


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
    # Verify authentication
    if not await verify_websocket_token(websocket):
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
        await websocket.send_json({
            "type": "connected",
            "task_id": task_id,
            "message": "Connected to log stream",
        })

        while True:
            try:
                # Wait for log with timeout to allow checking for disconnection
                log = await asyncio.wait_for(message_queue.get(), timeout=30.0)

                await websocket.send_json({
                    "type": "log",
                    "task_id": log.task_id,
                    "content": log.content,
                    "timestamp": log.timestamp,
                    "level": log.level,
                    "source": log.source,
                })

            except asyncio.TimeoutError:
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
    if not await verify_websocket_token(websocket):
        return

    await websocket.accept()

    agent_service = get_agent_service()
    message_queue: asyncio.Queue[TaskLog] = asyncio.Queue()
    unregister_callbacks: list[callable] = []

    async def log_callback(log: TaskLog):
        await message_queue.put(log)

    async def update_subscriptions():
        """Update subscriptions to match running tasks."""
        # Unregister all current callbacks
        for unregister in unregister_callbacks:
            unregister()
        unregister_callbacks.clear()

        # Register for all running tasks
        for task_id in agent_service.get_running_tasks():
            unregister = agent_service.register_log_callback(task_id, log_callback)
            unregister_callbacks.append(unregister)

    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to unified log stream",
        })

        last_task_check = asyncio.get_event_loop().time()

        while True:
            try:
                log = await asyncio.wait_for(message_queue.get(), timeout=5.0)

                await websocket.send_json({
                    "type": "log",
                    "task_id": log.task_id,
                    "content": log.content,
                    "timestamp": log.timestamp,
                    "level": log.level,
                    "source": log.source,
                })

            except asyncio.TimeoutError:
                # Periodically update subscriptions
                current_time = asyncio.get_event_loop().time()
                if current_time - last_task_check > 5.0:
                    await update_subscriptions()
                    last_task_check = current_time

                await websocket.send_json({"type": "heartbeat"})

    except WebSocketDisconnect:
        pass
    finally:
        for unregister in unregister_callbacks:
            unregister()
