"""Dispatch table for relayed WebSocket notifications (#804).

Maps a notification's ``kind`` (as published by ``relay.publish``, see
``events.py`` and ``services/agent_service.py``) back to the local-delivery
half that ran on the ORIGINATING pod, so THIS pod replays it for its own
locally-connected clients.

This is its own leaf module, not part of ``relay.py``: ``relay.py`` is a
generic Postgres LISTEN/NOTIFY transport that must not know about ``ws:*`` /
``task:*`` kinds, and ``services/agent_service.py`` already imports
``server.websockets.relay`` at module level (#804 step 3) — a module-level
import of ``agent_service`` back from ``relay.py`` would cycle. Living here
instead, ``_dispatch.py`` can import both ``events`` and ``agent_service``
at module level with no cycle: neither of those imports ``_dispatch``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from server.services.agent_service import (
    TaskLog,
    TaskPhase,
    TaskProgress,
    get_agent_service,
)

from . import events

logger = logging.getLogger(__name__)

_Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def _dispatch_broadcast(data: dict[str, Any]) -> None:
    await events._deliver_local_broadcast(data["event_type"], data["payload"])


async def _dispatch_to_user(data: dict[str, Any]) -> None:
    await events._deliver_local_to_user(data["user_id"], data["event_type"], data["payload"])


async def _dispatch_to_org(data: dict[str, Any]) -> None:
    await events._deliver_local_to_org(data["org_id"], data["event_type"], data["payload"])


async def _dispatch_task_log(data: dict[str, Any]) -> None:
    await get_agent_service()._deliver_local_log(TaskLog(**data))


async def _dispatch_task_progress(data: dict[str, Any]) -> None:
    # ``phase`` round-tripped through JSON as a plain string (TaskPhase is a
    # str Enum, so json.dumps already emitted its bare value) — rebuild the
    # enum so callbacks that read ``progress.phase.value`` still work
    # (e.g. websockets/progress.py).
    data = {**data, "phase": TaskPhase(data["phase"])}
    await get_agent_service()._deliver_local_progress(TaskProgress(**data))


# Unknown kinds already warned about. A rolling deploy puts an older pod beside a
# newer one for minutes, and the newer one may publish a kind this pod has never
# heard of — once per kind is a useful signal, once per notification would bury
# the log under the task-logs stream (#804).
_warned_unknown: set[str] = set()

_HANDLERS: dict[str, _Handler] = {
    "ws:broadcast": _dispatch_broadcast,
    "ws:user": _dispatch_to_user,
    "ws:org": _dispatch_to_org,
    "task:log": _dispatch_task_log,
    "task:progress": _dispatch_task_progress,
}


async def dispatch(kind: str, data: dict[str, Any]) -> None:
    """Replay a relayed notification locally. Passed as ``relay.start_listener``'s ``deliver``.

    Never raises: an unknown ``kind`` (a newer pod publishing something this
    pod doesn't yet know, during a rolling deploy) is logged once and ignored
    rather than killing the listener loop; likewise a handler that raises on
    a single bad payload must not stop delivery of every later event.
    """
    handler = _HANDLERS.get(kind)
    if handler is None:
        if kind not in _warned_unknown:
            _warned_unknown.add(kind)
            logger.warning("[relay] ignoring notifications with unknown kind=%s", kind)
        return

    try:
        await handler(data)
    except Exception:  # noqa: BLE001 — one bad payload must not kill the listener loop (#804)
        logger.warning("[relay] local delivery failed for kind=%s", kind, exc_info=True)
