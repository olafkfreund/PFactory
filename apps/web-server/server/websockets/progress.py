"""
WebSocket endpoint for streaming task progress.

Provides real-time progress updates during agent execution.
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import verify_websocket_token
from ..services.agent_service import TaskProgress, get_agent_service

router = APIRouter()


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
    if not await verify_websocket_token(websocket):
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
        await websocket.send_json({
            "type": "connected",
            "task_id": task_id,
            "is_running": is_running,
            "message": "Connected to progress stream",
        })

        while True:
            try:
                progress = await asyncio.wait_for(message_queue.get(), timeout=30.0)

                await websocket.send_json({
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
                })

            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({
                    "type": "heartbeat",
                    "is_running": agent_service.is_running(task_id),
                })

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
    if not await verify_websocket_token(websocket):
        return

    await websocket.accept()

    agent_service = get_agent_service()
    message_queue: asyncio.Queue[TaskProgress] = asyncio.Queue()
    unregister_callbacks: list[callable] = []

    async def progress_callback(progress: TaskProgress):
        await message_queue.put(progress)

    async def update_subscriptions():
        """Update subscriptions to match running tasks."""
        for unregister in unregister_callbacks:
            unregister()
        unregister_callbacks.clear()

        for task_id in agent_service.get_running_tasks():
            unregister = agent_service.register_progress_callback(task_id, progress_callback)
            unregister_callbacks.append(unregister)

    try:
        await update_subscriptions()

        await websocket.send_json({
            "type": "connected",
            "running_tasks": agent_service.get_running_tasks(),
            "message": "Connected to unified progress stream",
        })

        last_task_check = asyncio.get_event_loop().time()

        while True:
            try:
                progress = await asyncio.wait_for(message_queue.get(), timeout=5.0)

                await websocket.send_json({
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
                })

            except asyncio.TimeoutError:
                current_time = asyncio.get_event_loop().time()
                if current_time - last_task_check > 5.0:
                    await update_subscriptions()
                    last_task_check = current_time

                await websocket.send_json({
                    "type": "heartbeat",
                    "running_tasks": agent_service.get_running_tasks(),
                })

    except WebSocketDisconnect:
        pass
    finally:
        for unregister in unregister_callbacks:
            unregister()
