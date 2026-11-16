"""
WebSocket endpoint for terminal I/O.

Provides bidirectional communication between browser terminals (xterm.js)
and PTY processes on the server.
"""

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.auth import (
    WebSocketAuthError,
    _ws_acw_principal,
    _ws_extract_token,
    authenticate_websocket,
)
from server.pty.access import may_use
from server.pty.manager import get_pty_manager
from server.pty.session import PTYSession, create_pty_reader

router = APIRouter()
_RECHECK_SECONDS = 5.0


async def _watch(
    websocket: WebSocket,
    session: PTYSession,
    user: dict[str, Any] | None,
    token: str | None,
) -> None:
    """Close the socket when the key is revoked or the project is lost (#3740)."""
    while True:
        await asyncio.sleep(_RECHECK_SECONDS)
        if token and token.startswith("acw_") and user and user.get("api_key_id"):
            try:
                user = await _ws_acw_principal(token)
            except Exception:  # noqa: BLE001 - a failing validator counts as revoked
                user = None
            if user is None:
                await websocket.close(code=4001)
                return
        if not await may_use(user, session):
            await websocket.close(code=4003)
            return


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
    try:
        user = await authenticate_websocket(websocket)
    except WebSocketAuthError:
        return
    except Exception:  # noqa: BLE001 - unreadable auth state fails closed
        await websocket.close(code=4003)
        return

    await websocket.accept()

    manager = get_pty_manager()
    session = manager.get_session(terminal_id)
    # user None: legacy token or auth disabled, unrestricted as on all-task sockets.
    if session is not None and user is not None and not await may_use(user, session):
        session = None  # another user's terminal reads as missing

    if session is None:
        await websocket.send_json(
            {
                "type": "error",
                "message": f"Terminal {terminal_id} not found",
            }
        )
        await websocket.close(code=4004)
        return

    if not session.is_alive():
        await websocket.send_json(
            {
                "type": "error",
                "message": "Terminal process has exited",
            }
        )
        await websocket.close(code=4004)
        return

    # Send connection confirmation
    await websocket.send_json(
        {
            "type": "connected",
            "terminal_id": terminal_id,
            "cols": session.cols,
            "rows": session.rows,
        }
    )

    async def send_output(data: str):
        """Send PTY output to WebSocket."""
        try:
            await websocket.send_text(data)
        except Exception:
            pass

    # Start reader task
    reader_task = asyncio.create_task(create_pty_reader(session, send_output))
    watch_task = (
        asyncio.create_task(_watch(websocket, session, user, _ws_extract_token(websocket)))
        if user is not None
        else None
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
                                await websocket.send_json(
                                    {
                                        "type": "resized",
                                        "cols": cols,
                                        "rows": rows,
                                    }
                                )
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
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
        except Exception:
            pass

    finally:
        # Cancel reader task
        reader_task.cancel()
        if watch_task is not None:
            watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task
        if watch_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await watch_task

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
                await websocket.send_json(
                    {
                        "type": "exit",
                        "code": exit_code,
                        "message": "Terminal process exited",
                    }
                )
            except Exception:
                pass
