"""
WebSocket endpoint for terminal I/O.

Provides bidirectional communication between browser terminals (xterm.js)
and PTY processes on the server.
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth import verify_websocket_token
from ..pty.manager import get_pty_manager
from ..pty.session import create_pty_reader

router = APIRouter()


@router.websocket("/ws/terminal/{terminal_id}")
async def terminal_websocket(websocket: WebSocket, terminal_id: str):
    """WebSocket endpoint for terminal I/O.

    Protocol:
    - Text messages are written directly to the PTY
    - JSON messages with {"type": "resize", "cols": N, "rows": M} resize the terminal
    - Server sends terminal output as text messages
    - Server sends {"type": "exit", "code": N} when terminal exits
    - Server sends {"type": "error", "message": "..."} on errors
    """
    if not await verify_websocket_token(websocket):
        return

    await websocket.accept()

    manager = get_pty_manager()
    session = manager.get_session(terminal_id)

    if session is None:
        await websocket.send_json({
            "type": "error",
            "message": f"Terminal {terminal_id} not found",
        })
        await websocket.close(code=4004)
        return

    if not session.is_alive():
        await websocket.send_json({
            "type": "error",
            "message": "Terminal process has exited",
        })
        await websocket.close(code=4004)
        return

    # Send connection confirmation
    await websocket.send_json({
        "type": "connected",
        "terminal_id": terminal_id,
        "cols": session.cols,
        "rows": session.rows,
    })

    async def send_output(data: str):
        """Send PTY output to WebSocket."""
        try:
            await websocket.send_text(data)
        except Exception:
            pass

    # Start reader task
    reader_task = asyncio.create_task(
        create_pty_reader(session, send_output)
    )

    try:
        while True:
            try:
                message = await websocket.receive()

                if message["type"] == "websocket.disconnect":
                    break

                if "text" in message:
                    text = message["text"]

                    # Check if it's a JSON control message
                    if text.startswith("{"):
                        try:
                            data = json.loads(text)
                            msg_type = data.get("type")

                            if msg_type == "resize":
                                cols = data.get("cols", 80)
                                rows = data.get("rows", 24)
                                session.resize(cols, rows)
                                await websocket.send_json({
                                    "type": "resized",
                                    "cols": cols,
                                    "rows": rows,
                                })
                                continue

                            elif msg_type == "ping":
                                await websocket.send_json({"type": "pong"})
                                continue

                            elif msg_type == "interrupt":
                                session.interrupt()
                                continue

                        except json.JSONDecodeError:
                            pass

                    # Regular text input - write to PTY
                    session.write(text)

                elif "bytes" in message:
                    # Binary data - decode and write
                    session.write(message["bytes"].decode("utf-8", errors="replace"))

            except WebSocketDisconnect:
                break

    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception:
            pass

    finally:
        # Cancel reader task
        reader_task.cancel()
        try:
            await reader_task
        except asyncio.CancelledError:
            pass

        # Check if terminal exited
        if not session.is_alive():
            try:
                # Try to get actual exit code from the PTY process
                exit_code = 0
                if session._pty is not None:
                    try:
                        # ptyprocess provides exitstatus after process exits
                        exit_code = session._pty.exitstatus or 0
                    except Exception:
                        pass
                await websocket.send_json({
                    "type": "exit",
                    "code": exit_code,
                    "message": "Terminal process exited",
                })
            except Exception:
                pass
