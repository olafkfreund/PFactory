"""
WebSocket endpoint for streaming task progress.

Provides real-time progress updates during agent execution.
"""

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.services.agent_service import TaskProgress, get_agent_service

from .task_access import AllTaskSocketAccess, TaskSocketAccess

router = APIRouter()
_REFRESH_SECONDS = 5.0


@router.websocket("/ws/tasks/{task_id}/progress")
async def task_progress_websocket(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task progress.

    Connect to receive real-time progress updates from a running task.
    Progress updates are sent as JSON objects with the following structure:
    {
        "task_id": "string",
        "phase": "spec_creation|planning|coding|qa_review|qa_fixing|completed|failed",
        "message": "string",
        "timestamp": "ISO timestamp",
        "subtask": "string (optional)",
        "subtask_index": number (optional),
        "subtask_total": number (optional),
        "percentage": number (optional),
        "data": {} (optional additional data)
    }
    """
    access = TaskSocketAccess(websocket, task_id)
    if not await access.connect():
        return

    await websocket.accept()

    agent_service = get_agent_service()
    message_queue: asyncio.Queue[TaskProgress] = asyncio.Queue()

    async def progress_callback(progress: TaskProgress):
        await message_queue.put(progress)

    # Register callback
    unregister = agent_service.register_progress_callback(task_id, progress_callback)

    try:
        # Send initial connection message with current status
        is_running = agent_service.is_running(task_id)
        await websocket.send_json(
            {
                "type": "connected",
                "task_id": task_id,
                "is_running": is_running,
                "message": "Connected to progress stream",
            }
        )

        while True:
            if not await access.recheck():
                return
            try:
                progress = await asyncio.wait_for(message_queue.get(), timeout=30.0)

                await websocket.send_json(
                    {
                        "type": "progress",
                        "task_id": progress.task_id,
                        "phase": progress.phase.value,
                        "message": progress.message,
                        "timestamp": progress.timestamp,
                        "subtask": progress.subtask,
                        "subtask_index": progress.subtask_index,
                        "subtask_total": progress.subtask_total,
                        "percentage": progress.percentage,
                        "data": progress.data,
                    }
                )

            except TimeoutError:
                # Send heartbeat
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "is_running": agent_service.is_running(task_id),
                    }
                )

    except WebSocketDisconnect:
        pass
    finally:
        unregister()


@router.websocket("/ws/progress")
async def all_progress_websocket(websocket: WebSocket):
    """WebSocket endpoint for streaming progress from ALL running tasks.

    Connect to receive real-time progress from all running tasks.
    Useful for a unified progress dashboard.
    """
    access = AllTaskSocketAccess(websocket)
    if not await access.connect():
        return

    await websocket.accept()

    agent_service = get_agent_service()
    message_queue: asyncio.Queue[TaskProgress] = asyncio.Queue()
    # `list[callable]` named the BUILTIN `callable()`, which is not a type:
    # mypy rejected it outright, so every reader got a guarantee nothing
    # checked. register_progress_callback returns a no-argument unregister
    # closure (agent_service.py:103).
    unregister_callbacks: list[Callable[[], None]] = []
    allowed: set[str] = set()

    async def progress_callback(progress: TaskProgress):
        await message_queue.put(progress)

    async def refresh_subscriptions() -> bool:
        """Update subscriptions to match running tasks."""
        nonlocal allowed
        if not await access.recheck():
            return False
        fresh = await access.readable(agent_service.get_running_tasks())
        for unregister in unregister_callbacks:
            unregister()
        unregister_callbacks.clear()

        allowed = fresh
        for task_id in allowed:
            unregister = agent_service.register_progress_callback(task_id, progress_callback)
            unregister_callbacks.append(unregister)
        return True

    try:
        if not await refresh_subscriptions():
            return

        await websocket.send_json(
            {
                "type": "connected",
                "running_tasks": [t for t in agent_service.get_running_tasks() if t in allowed],
                "message": "Connected to unified progress stream",
            }
        )

        loop = asyncio.get_running_loop()
        last_refresh = loop.time()

        while True:
            try:
                progress = await asyncio.wait_for(
                    message_queue.get(),
                    timeout=max(0.0, _REFRESH_SECONDS - (loop.time() - last_refresh)),
                )
            except TimeoutError:
                progress = None

            if loop.time() - last_refresh >= _REFRESH_SECONDS:
                if not await refresh_subscriptions():
                    return
                last_refresh = loop.time()
            if progress is None:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "running_tasks": [
                            t for t in agent_service.get_running_tasks() if t in allowed
                        ],
                    }
                )
            # a finished task leaves running_tasks before its last event is processed: re-authorize
            elif progress.task_id in allowed or await access.readable([progress.task_id]):
                await websocket.send_json(
                    {
                        "type": "progress",
                        "task_id": progress.task_id,
                        "phase": progress.phase.value,
                        "message": progress.message,
                        "timestamp": progress.timestamp,
                        "subtask": progress.subtask,
                        "subtask_index": progress.subtask_index,
                        "subtask_total": progress.subtask_total,
                        "percentage": progress.percentage,
                        "data": progress.data,
                    }
                )

    except WebSocketDisconnect:
        pass
    finally:
        for unregister in unregister_callbacks:
            unregister()
