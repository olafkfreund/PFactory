"""Cross-pod WebSocket fan-out via Postgres LISTEN/NOTIFY (#804).

Each pod keeps its own ``asyncio`` process memory of connected
WebSocket clients, so an event emitted on pod A never reaches a
browser connected to pod B. Rather than add a dependency (Redis, a
message broker), this relays events through the Postgres the app
already requires: publish with ``pg_notify``, and every pod's listener
delivers notifications from OTHER pods into its own local delivery
functions.

Local delivery always happens first and unconditionally in the
caller (``events.py`` / ``agent_service.py``); this module is ONLY the
inter-pod hop. A relay failure must never take down the local path —
``publish`` never raises.

Inert without ``DATABASE_URL`` (single-process and test deployments
behave exactly as before #804): no connection is opened, ``publish``
is a no-op, and ``start_listener`` logs once and returns.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # no py.typed marker upstream

logger = logging.getLogger(__name__)

# Identifies notifications this process itself published, so the listener
# can skip them — the emitting pod already delivered locally; replaying its
# own event back to itself would double-deliver. HOSTNAME (pod name) plus a
# per-process nonce so two pods that briefly share a hostname (a restart
# during a rolling deploy) still cannot collide.
_ORIGIN = f"{os.environ.get('HOSTNAME', 'local')}:{uuid4().hex[:8]}"

_CHANNEL = "pfactory_ws"

# Postgres caps a NOTIFY payload at 8000 bytes. 7900 leaves headroom for the
# channel name and protocol framing around the payload we control.
_MAX_PAYLOAD = 7900

# Capped backoff for the listener's reconnect loop, in seconds.
_RECONNECT_BACKOFF = (1, 2, 5, 10, 30)

# The relay's own asyncpg connection, held only while the listener is
# running. ``publish`` uses it too, so a single connection carries both
# directions for this process.
_connection: asyncpg.Connection | None = None
_listener_task: asyncio.Task[None] | None = None

# asyncpg.Connection is not safe for concurrent operations — two overlapping
# statements on it raise InterfaceError. With 63 emit call sites in an async
# app, concurrent ``publish`` calls are the normal case, so serialise the one
# statement per call under a lock rather than add a connection pool for a
# single-row NOTIFY (#804).
_publish_lock = asyncio.Lock()


def _encode(kind: str, data: dict[str, Any]) -> str | None:
    """JSON-encode a notification, truncating oversized content to fit.

    Returns ``None`` if the payload still doesn't fit after truncation —
    the caller drops it rather than crash the pipeline over a lost log line.
    """
    payload: dict[str, Any] = {"origin": _ORIGIN, "kind": kind, "data": data}
    encoded = json.dumps(payload)
    if len(encoded.encode()) <= _MAX_PAYLOAD:
        return encoded

    # Truncate the one field we know can be large: ``content``, either at
    # the top level (task:log-style events) or nested under ``chunk``
    # (task-logs:stream events). Work on a copy — callers still hold the
    # original ``data`` dict and must not see it mutated.
    payload = copy.deepcopy(payload)
    truncated_data: dict[str, Any] = payload["data"]
    target: dict[str, Any] = truncated_data
    if "content" not in target and isinstance(target.get("chunk"), dict):
        target = target["chunk"]
    content = target.get("content")
    if not isinstance(content, str):
        return None

    # Binary-search-free: shrink content until the envelope fits, accounting
    # for the rest of the envelope's own byte cost.
    truncated_data["truncated"] = True
    overhead = len(json.dumps(payload).encode()) - len(content.encode())
    budget = _MAX_PAYLOAD - overhead
    while budget > 0:
        target["content"] = content[:budget]
        encoded = json.dumps(payload)
        if len(encoded.encode()) <= _MAX_PAYLOAD:
            return encoded
        # json.dumps escapes some characters (e.g. quotes, backslashes,
        # unicode), which can inflate encoded length beyond raw truncation —
        # shrink further and retry.
        budget -= 64

    return None


async def publish(kind: str, data: dict[str, Any]) -> None:
    """Publish an event to other pods. No-op when the relay isn't started.

    Never raises: a DB hiccup must not silence a browser attached to the
    pod that emitted the event, which has already been delivered locally
    by the caller before this runs.
    """
    if _connection is None:
        return

    encoded = _encode(kind, data)
    if encoded is None:
        logger.warning("[relay] dropping oversized %s notification, could not fit under cap", kind)
        return

    try:
        async with _publish_lock:
            await _connection.execute("SELECT pg_notify($1, $2)", _CHANNEL, encoded)
    except Exception:  # noqa: BLE001 — a DB hiccup must not silence the local browser (#804)
        logger.warning("[relay] publish failed for kind=%s", kind, exc_info=True)


def _resolve_asyncpg_url() -> str | None:
    """The raw ``DATABASE_URL``, in asyncpg's own dialect-less form.

    Returns ``None`` when unset — the relay's inert path. Deliberately reads
    the env var directly rather than going through
    ``server.database.engine._resolve_database_url``, which falls back to a
    SQLite URL for local dev; that fallback is right for SQLAlchemy but
    would hand asyncpg a URL it cannot connect with.
    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        return None
    return raw.replace("+asyncpg", "")


async def start_listener(deliver: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
    """Start the relay: connect, LISTEN, and dispatch other pods' events.

    Reconnects with capped backoff on connection loss so a transient DB
    blip doesn't permanently strand this pod out of the fan-out. Inert (and
    logged once at INFO) when ``DATABASE_URL`` is unset.
    """
    global _listener_task  # noqa: PLW0603 — module-level relay state (#804)

    url = _resolve_asyncpg_url()
    if url is None:
        logger.info("[relay] DATABASE_URL not set, WebSocket fan-out relay is inert")
        return

    async def _run() -> None:
        global _connection  # noqa: PLW0603 — module-level relay state (#804)
        attempt = 0
        while True:
            try:
                conn = await asyncpg.connect(url)
                _connection = conn
                attempt = 0

                queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

                def _on_notify(
                    _conn: object,
                    _pid: int,
                    _channel: str,
                    payload: str,
                    _queue: asyncio.Queue[tuple[str, str]] = queue,
                ) -> None:
                    _queue.put_nowait((_channel, payload))

                await conn.add_listener(_CHANNEL, _on_notify)
                try:
                    while True:
                        _channel, payload = await queue.get()
                        try:
                            message = json.loads(payload)
                        except ValueError:
                            logger.warning("[relay] dropping malformed notification payload")
                            continue
                        if message.get("origin") == _ORIGIN:
                            continue
                        await deliver(message.get("kind", ""), message.get("data", {}))
                finally:
                    await conn.remove_listener(_CHANNEL, _on_notify)
                    await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — reconnect on any failure, capped backoff below (#804)
                logger.warning("[relay] listener connection lost, reconnecting", exc_info=True)
            finally:
                _connection = None

            delay = _RECONNECT_BACKOFF[min(attempt, len(_RECONNECT_BACKOFF) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    _listener_task = asyncio.create_task(_run())


async def stop_listener() -> None:
    """Stop the relay's listener task and close its connection."""
    global _connection, _listener_task  # noqa: PLW0603 — module-level relay state (#804)

    if _listener_task is not None:
        _listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _listener_task
        _listener_task = None

    if _connection is not None:
        await _connection.close()
        _connection = None
