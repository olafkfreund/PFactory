"""WebSocket bridge between rmux pane FIFOs and browser xterm.js (Epic #44).

Two endpoints, both gated by ``PFACTORY_RMUX_ENABLED``:

  GET  /api/tasks/{task_id}/agent-console/ws       (WebSocket)
       Streams pane bytes FIFO→browser.  In attach mode, also accepts
       browser keystrokes and forwards via ``rmux send-keys``.

  POST /api/tasks/{task_id}/agent-console/attach   (JSON)
       Body: ``{"connection_id": "..."}``.  Flips the named WS
       connection into bidirectional mode AND writes an
       ``audit.action=console.attach`` row.  At most one attached
       connection per session — concurrent POSTs lose to 409 Conflict.

The race-safe attach contract (design §3.1):

  1. WS server generates ``connection_id`` (UUID v4) on accept, sends
     it as the first ``{"type":"connected","connection_id":...}`` frame
  2. ``POST /attach`` acquires the per-session ``asyncio.Lock``
  3. If ``state.attached_connection_id is None``: set it to the
     request body's connection_id, write audit row, release lock,
     return 200
  4. Else: release lock, return 409 Conflict
  5. WS receive loop polls ``state.attached_connection_id == self.cid``
     to decide whether to forward inbound bytes
  6. On WS disconnect or ``POST /detach``: clear under the same lock,
     write ``console.detach`` audit row

Browser→pane byte encoding: xterm.js sends raw key bytes (e.g. ``\\x1b[A``
for up-arrow, ``\\x03`` for Ctrl-C, plain UTF-8 for printable text).
We forward via ``send_keys`` (no ``-l``) so rmux interprets control
sequences correctly.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import verify_websocket_token
from ..database.engine import get_db
from ..services.audit_service import log_audit_event
from .session import SessionState, get_registry
from .wrapper import RmuxError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["rmux Live Console"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_fifo_chunks(fifo_path: Path, chunk: int = 4096):
    """Async generator yielding bytes from ``fifo_path`` until close/EOF.

    Wraps the blocking read in ``asyncio.to_thread`` so the WS event
    loop never stalls on a slow pane.  Opening in binary mode preserves
    ANSI escape bytes intact for xterm.js.
    """

    def _open_blocking():
        return open(fifo_path, "rb", buffering=0)

    fh = await asyncio.to_thread(_open_blocking)
    try:
        while True:
            data = await asyncio.to_thread(fh.read, chunk)
            if not data:
                # FIFO writer closed.  In practice rmux's pipe-pane keeps
                # the writer open for the session's lifetime, so EOF
                # means the session was killed — bail.
                return
            yield data
    finally:
        try:
            fh.close()
        except OSError:
            pass


def _resolve_state_or_404(spec_id: str) -> SessionState:
    """Look up a session in the registry; raise 404 if missing.

    ``spec_id`` may arrive as a composite ``project_id:spec_id`` from
    older frontend routes.  We split on the first colon if present so
    the user can paste either form.
    """
    registry = get_registry()
    state = registry.get_state(spec_id)
    if state is None and ":" in spec_id:
        # Try the suffix half — some frontend routes pass the
        # ``project_id:spec_id`` form
        state = registry.get_state(spec_id.split(":", 1)[1])
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"no rmux session registered for {spec_id}",
        )
    return state


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AttachRequest(BaseModel):
    """Body for ``POST /attach``.

    ``connection_id`` MUST match the value the server sent on the WS
    handshake's first frame — that's how we bind the audit row + the
    attach right to a specific browser tab.
    """

    connection_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="UUID v4 from the WS handshake's `connected` frame",
    )


# ---------------------------------------------------------------------------
# REST: POST /attach   POST /detach
# ---------------------------------------------------------------------------


@router.post("/{spec_id}/agent-console/attach")
async def attach(
    spec_id: str,
    body: AttachRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Flip the named connection into bidirectional input mode.

    On success: 200, writes ``audit.action=console.attach`` row.
    On race lost: 409, no audit row.
    On unknown spec: 404.

    The audit row binds (user_id, org_id, ip, connection_id, spec_id)
    so an investigator can answer "who attached when?" for any session.
    """
    state = _resolve_state_or_404(spec_id)
    cid = body.connection_id

    # Audit-relevant metadata — pulled from middleware-set request state
    # rather than the body so a malicious client can't lie about who
    # they are.  Mirror existing audit callsites in routes/audit.py.
    user_id = getattr(request.state, "user_id", None)
    org_id = getattr(request.state, "org_id", None)
    client_ip = request.client.host if request.client else None

    async with state.lock:
        if state.attached_connection_id is not None:
            # Someone already holds attach.  Return 409 with enough
            # context that the UI can render "another session has
            # control" without leaking the other user's identity.
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "session_already_attached",
                    "attached_connection_id": state.attached_connection_id,
                },
            )
        state.attached_connection_id = cid

    # Audit row OUTSIDE the lock — DB I/O is slow and we don't want to
    # serialise other tasks' lock acquisitions behind it.  Worst case
    # if the DB write fails, the attach is already in effect (the
    # in-memory flag flipped); the warning log is the operator's
    # signal something went wrong.  The audit_service helper already
    # wraps writes in try/except for exactly this reason.
    if db is not None:
        await log_audit_event(
            db,
            user_id=user_id,
            org_id=org_id,
            action="console.attach",
            resource_type="task",
            resource_id=state.spec_id,
            details={"connection_id": cid, "session_name": state.session_name},
            ip=client_ip,
        )

    return {"status": "attached", "connection_id": cid}


@router.post("/{spec_id}/agent-console/detach")
async def detach(
    spec_id: str,
    body: AttachRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Release attach mode held by ``connection_id``.

    Only the holder can detach (gates against a hostile client
    detaching someone else's session).  Writes ``console.detach``.

    Returns 200 even if the connection wasn't the holder, so the WS
    disconnect cleanup path can be fire-and-forget — that case is
    benign (race between client close and explicit detach).
    """
    state = _resolve_state_or_404(spec_id)
    cid = body.connection_id

    user_id = getattr(request.state, "user_id", None)
    org_id = getattr(request.state, "org_id", None)
    client_ip = request.client.host if request.client else None

    released = False
    async with state.lock:
        if state.attached_connection_id == cid:
            state.attached_connection_id = None
            released = True

    if released and db is not None:
        await log_audit_event(
            db,
            user_id=user_id,
            org_id=org_id,
            action="console.detach",
            resource_type="task",
            resource_id=state.spec_id,
            details={"connection_id": cid, "session_name": state.session_name},
            ip=client_ip,
        )

    return {"status": "detached" if released else "not_holder"}


# ---------------------------------------------------------------------------
# WebSocket: bidirectional pane bridge
# ---------------------------------------------------------------------------


@router.websocket("/{spec_id}/agent-console/ws")
async def agent_console_ws(websocket: WebSocket, spec_id: str):
    """Stream pane bytes FIFO→browser; accept browser keys when attached.

    Protocol:
      - First server frame: ``{"type":"connected","connection_id":"..."}``
        The client stores this UUID and includes it in any subsequent
        ``POST /attach`` call.
      - Subsequent server frames: raw binary pane bytes (ANSI intact).
      - Client→server frames: raw binary keystrokes; forwarded to
        ``rmux send-keys`` ONLY when this connection holds attach mode.
        Otherwise silently dropped (with a debug log) — read-only
        viewers MUST NOT be able to type by accident.

    Auth: ``verify_websocket_token`` is the same gate ``terminal.py``
    uses.  Adding org-membership check is a v1.1 follow-up (when we add
    cross-org pane sharing).
    """
    if not await verify_websocket_token(websocket):
        return

    try:
        state = _resolve_state_or_404(spec_id)
    except HTTPException:
        await websocket.accept()
        await websocket.close(code=4004, reason="no rmux session for spec_id")
        return

    await websocket.accept()

    # Generate the connection_id and send it as the first frame so the
    # client can use it for /attach.
    cid = str(uuid.uuid4())
    await websocket.send_json({"type": "connected", "connection_id": cid})

    registry = get_registry()
    wrapper = registry.wrapper

    # Spawn two concurrent tasks:
    #  - reader: pump FIFO bytes → WS (always running)
    #  - writer_listener: receive WS frames; if attach is held by us,
    #    forward to send-keys.  Otherwise drop.
    async def _reader():
        try:
            async for chunk in _read_fifo_chunks(state.fifo_path):
                await websocket.send_bytes(chunk)
        except WebSocketDisconnect:
            return
        except Exception:
            logger.warning(
                "agent-console reader crashed for %s",
                state.spec_id,
                exc_info=True,
            )

    async def _writer_listener():
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                data = msg.get("bytes") or msg.get("text")
                if not data:
                    continue
                # Drop silently when not in attach mode.
                if state.attached_connection_id != cid:
                    logger.debug(
                        "dropping read-only WS input for %s (attached=%s, this=%s)",
                        state.spec_id,
                        state.attached_connection_id,
                        cid,
                    )
                    continue
                # Forward.  Convert bytes→str if necessary; rmux
                # send-keys accepts ESC sequences as raw text on stdin.
                payload = (
                    data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
                )
                try:
                    await wrapper.send_keys(state.session_name, payload)
                except RmuxError:
                    logger.warning(
                        "send-keys failed for %s (session gone?)",
                        state.spec_id,
                        exc_info=True,
                    )
        except WebSocketDisconnect:
            return

    reader_task = asyncio.create_task(_reader())
    writer_task = asyncio.create_task(_writer_listener())
    try:
        done, pending = await asyncio.wait(
            {reader_task, writer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        # Release attach mode if this connection held it — otherwise
        # the next attach POST would 409 forever.
        async with state.lock:
            if state.attached_connection_id == cid:
                state.attached_connection_id = None
        try:
            await websocket.close()
        except Exception:
            pass
