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
# running. The outbox drain task below uses it too, so a single connection
# carries both directions for this process.
_connection: asyncpg.Connection | None = None
_listener_task: asyncio.Task[None] | None = None
_drain_task: asyncio.Task[None] | None = None

# Bounds both queues in this module (#804 finding 4 / finding 5). A few
# thousand is plenty for either direction under normal operation — it exists
# to cap MEMORY during an outage (a stuck Postgres, a wedged local client),
# not to throttle a healthy system. 2000 encoded notifications at up to
# ``_MAX_PAYLOAD`` bytes each is a worst case of ~15MB, which is a price
# worth paying to never grow without bound.
_QUEUE_MAXSIZE = 2000


def _locate_truncatable_content(data: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Find the (dict, key) holding the one field we truncate to fit.

    Explicit candidates, checked in this exact order — not a generic
    "find the biggest string" search, so a reader can see every event shape
    this covers, and extending it means adding a line here, not guessing
    (#804 fix 3):
      - ``data["content"]``                    — task:log
      - ``data["payload"]["chunk"]["content"]`` — ws:broadcast relaying
        ``task-logs:stream`` (``events.py`` puts the chunk at
        ``data["payload"]["chunk"]``, not top-level ``data["chunk"]``)
      - ``data["chunk"]["content"]``            — kept in case something
        ever publishes with ``chunk`` directly under ``data``
      - ``data["payload"]["content"]``          — a bare ws:broadcast payload
    """
    if isinstance(data.get("content"), str):
        return data, "content"

    payload_field = data.get("payload")
    if isinstance(payload_field, dict):
        chunk = payload_field.get("chunk")
        if isinstance(chunk, dict) and isinstance(chunk.get("content"), str):
            return chunk, "content"

    chunk_field = data.get("chunk")
    if isinstance(chunk_field, dict) and isinstance(chunk_field.get("content"), str):
        return chunk_field, "content"

    if isinstance(payload_field, dict) and isinstance(payload_field.get("content"), str):
        return payload_field, "content"

    return None


def _encode(kind: str, data: dict[str, Any]) -> str | None:
    """JSON-encode a notification, truncating oversized content to fit.

    Returns ``None`` if the payload still doesn't fit after truncation —
    the caller drops it rather than crash the pipeline over a lost log line.
    """
    payload: dict[str, Any] = {"origin": _ORIGIN, "kind": kind, "data": data}
    encoded = json.dumps(payload)
    if len(encoded.encode()) <= _MAX_PAYLOAD:
        return encoded

    # Work on a copy — callers still hold the original ``data`` dict and
    # must not see it mutated.
    payload = copy.deepcopy(payload)
    located = _locate_truncatable_content(payload["data"])
    if located is None:
        return None
    target, key = located
    content = target[key]

    # The flag lives on the ENVELOPE, not inside ``data`` (#804 fix 2):
    # ``data`` is a faithful dump of the model the receiving pod rebuilds
    # (e.g. ``TaskLog(**data)``), which has no ``truncated`` field — putting
    # it there made that rebuild raise ``TypeError``, silently dropping the
    # very payload truncation exists to save.
    payload["truncated"] = True

    # Binary-search-free: shrink content until the envelope fits, accounting
    # for the rest of the envelope's own byte cost.
    overhead = len(json.dumps(payload).encode()) - len(content.encode())
    budget = _MAX_PAYLOAD - overhead
    while budget > 0:
        target[key] = content[:budget]
        encoded = json.dumps(payload)
        if len(encoded.encode()) <= _MAX_PAYLOAD:
            return encoded
        # json.dumps escapes some characters (e.g. quotes, backslashes,
        # unicode), which can inflate encoded length beyond raw truncation —
        # shrink further and retry.
        budget -= 64

    return None


def is_connected() -> bool:
    """True only when the relay holds an ESTABLISHED, live connection.

    Distinguishes "connected and actually relaying" from "a listener task
    was scheduled and is still retrying `connect()`" — `_listener_task is
    not None` alone can't tell those apart, and an operator debugging
    "events aren't arriving on pod B" needs to (#804 fix 7).
    """
    return _connection is not None and not _connection.is_closed()


# The outbox: `publish` only encodes and enqueues; one drain task (started
# by `start_listener`, stopped by `stop_listener`) does the actual `execute`
# (#804 finding 4). Before this, `publish` awaited `execute` directly under a
# lock, which meant every agent stdout line paid for a Postgres round-trip,
# serialised process-wide, INSIDE the stdout reader — unconditionally, even
# with no other pod listening. Queueing decouples "emit a log line" from
# "a NOTIFY reaches Postgres" the same way local delivery is already
# decoupled from the relay hop.
# ``None`` until `start_listener` creates it. A module-level
# ``asyncio.Queue`` created once at import time binds to whichever event
# loop happens to be running the first time it's used and then errors on any
# other loop — fatal for tests, which give each test its own loop — so this
# is (re)created fresh inside `start_listener`, on the loop that will
# actually use it, exactly like `_listen_on`'s own inbound queue already is.
_outbox: asyncio.Queue[str] | None = None

# Dedupes the outbox-full warning the same way `_dispatch.py` dedupes
# unknown-kind warnings — a sustained flood must not become a log flood.
# Reset on the next successful enqueue so a LATER, separate flood still warns.
_outbox_full_warned = False


async def publish(kind: str, data: dict[str, Any]) -> None:
    """Encode and enqueue an event for other pods. No-op when the relay isn't
    started. Never blocks on Postgres and never raises.

    Never raises: a DB hiccup must not silence a browser attached to the
    pod that emitted the event, which has already been delivered locally
    by the caller before this runs. Beyond that, since #804 finding 4, this
    function no longer talks to Postgres at all — it can only fail by the
    outbox being full, which it handles by dropping and logging (below).
    That means a real drain failure (a dead connection, a Postgres hiccup)
    is no longer reported to THIS caller — it never could act on it anyway
    (local delivery already happened) — so the drain task's own logging
    (see `_drain_outbox`) is now the only signal a failure happened at all.

    Bounded no-op window (#804 fix 8): between `start_listener` returning
    and the background connect landing — and again, briefly, on every
    reconnect — nothing is listening on the outbox yet. Unlike before #804
    finding 4, publishes during that window are now QUEUED, not dropped —
    the drain task catches up once connected — so this window shrank to
    "relay never started at all" (`_drain_task is None`, the check below).
    `is_connected()` above is how a caller can see whether the underlying
    connection is currently live.
    """
    global _outbox_full_warned  # noqa: PLW0603 — module-level relay state (#804)

    outbox = _outbox
    if _drain_task is None or outbox is None:
        return

    encoded = _encode(kind, data)
    if encoded is None:
        logger.warning("[relay] dropping oversized %s notification, could not fit under cap", kind)
        return

    try:
        outbox.put_nowait(encoded)
    except asyncio.QueueFull:
        if not _outbox_full_warned:
            _outbox_full_warned = True
            logger.warning(
                "[relay] outbox full (%d), dropping notifications until it drains", _QUEUE_MAXSIZE
            )
        return
    _outbox_full_warned = False


async def _drain_outbox(outbox: asyncio.Queue[str]) -> None:
    """The single task that actually talks to Postgres for publishing.

    One task draining one queue onto one connection serialises writes by
    construction — the `_publish_lock` this replaces is dead weight once
    this is the only caller of `execute` (#804 finding 4).

    A drain failure must not kill this task: if it did, every `publish`
    after the first failure would queue forever with nothing ever draining
    it. Log and keep draining — the next notification gets its own attempt.
    """
    while True:
        encoded = await outbox.get()
        if _connection is None:
            # No live connection right now (mid-reconnect, or between
            # start_listener returning and the first connect landing) --
            # drop it. Holding it would just delay every notification queued
            # after it once a connection does land, and the caller's local
            # delivery already happened regardless.
            continue
        try:
            await _connection.execute("SELECT pg_notify($1, $2)", _CHANNEL, encoded)
        except Exception:  # noqa: BLE001 — must not kill the drain task (#804 finding 4)
            logger.warning("[relay] outbox drain failed", exc_info=True)


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


# Backstop for the inner loop's liveness check, in seconds. The termination
# listener below (`_on_terminate`) is the FAST path: asyncpg calls it the
# instant it detects the connection died, which is what fires in the common
# case. But it is a callback, not a guarantee — a half-open socket, a
# network partition where no FIN ever arrives, or some asyncpg code path
# that simply doesn't invoke it, and nothing ever lands on the queue to wake
# this loop up. `conn.is_closed()` was already `True` the instant a real
# reproduction (`pg_terminate_backend`) killed the backend, so wrapping
# `queue.get()` in a bounded wait and re-checking `is_closed()` on timeout
# means the loop notices even when the callback never fires (#804 fix 1). A
# few seconds, not sub-second: this is a liveness backstop for the rare case
# the fast path misses, not a hot loop — an operator would not notice a few
# seconds of extra delay in the already-rare "callback didn't fire" case.
_POLL_INTERVAL = 5.0


# Dedupes the inbound-queue-full warning, same reasoning as
# `_outbox_full_warned` (#804 finding 5).
_inbound_full_warned = False


def _put_inbound_dropping_oldest(
    queue: asyncio.Queue[tuple[str, str] | None], item: tuple[str, str] | None
) -> None:
    """Enqueue, evicting the OLDEST entry first if the queue is full.

    Drop-OLDEST, not drop-newest: the value of a queued inbound notification
    decays with how long it's already been sitting there — the newest
    task:log/task:progress tick is far more useful to a pod that's catching
    up than one that arrived seconds earlier in an already-backlogged queue —
    and evicting the head of a bounded FIFO queue is O(1) either way. This
    also means the termination sentinel always gets in: if the queue is
    full, this makes room for it rather than dropping it (#804 finding 5).
    """
    global _inbound_full_warned  # noqa: PLW0603 — module-level relay state (#804)

    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        queue.put_nowait(item)
        if not _inbound_full_warned:
            _inbound_full_warned = True
            logger.warning(
                "[relay] inbound queue full (%d), dropping oldest notification", _QUEUE_MAXSIZE
            )
        return
    _inbound_full_warned = False


async def _listen_on(
    conn: asyncpg.Connection, deliver: Callable[[str, dict[str, Any]], Awaitable[None]]
) -> None:
    """Run one LISTEN session on an already-connected ``conn`` until it dies.

    Returns normally (rather than raising) both on a graceful stop and on
    the connection dying — either way, the caller's reconnect loop is what
    decides what happens next.
    """
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

    def _on_notify(
        _conn: object,
        _pid: int,
        _channel: str,
        payload: str,
        _queue: asyncio.Queue[tuple[str, str] | None] = queue,
    ) -> None:
        _put_inbound_dropping_oldest(_queue, (_channel, payload))

    def _on_terminate(_conn: object, _queue: asyncio.Queue[tuple[str, str] | None] = queue) -> None:
        _put_inbound_dropping_oldest(_queue, None)

    conn.add_termination_listener(_on_terminate)
    await conn.add_listener(_CHANNEL, _on_notify)
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_POLL_INTERVAL)
            except TimeoutError:
                if conn.is_closed():
                    break
                continue
            # The sentinel from ``_on_terminate``, or a defensive
            # belt-and-suspenders check in case the connection died without
            # the callback firing on this particular path.
            if item is None or conn.is_closed():
                break
            _channel, payload = item
            try:
                message = json.loads(payload)
            except ValueError:
                logger.warning("[relay] dropping malformed notification payload")
                continue
            if not isinstance(message, dict):
                # A well-formed JSON value that isn't an object — e.g. a
                # bare number or string — decodes without error but has no
                # ``.get``. Drop it like a malformed payload (#804 fix 9b)
                # rather than let the AttributeError below escape uncaught
                # and cost this pod its listener connection over one bad
                # notification.
                logger.warning("[relay] dropping notification payload that isn't a JSON object")
                continue
            if message.get("origin") == _ORIGIN:
                continue
            await deliver(message.get("kind", ""), message.get("data", {}))
    finally:
        conn.remove_termination_listener(_on_terminate)
        with contextlib.suppress(Exception):
            # The connection may already be dead (that's exactly why we're
            # here) — closing/unlistening a dead connection is expected, not
            # a new failure to report. Deliberately ``Exception``, not
            # ``BaseException``: a genuine cancellation must still propagate
            # (#804 fix 6), and ``contextlib.suppress`` already leaves
            # ``CancelledError`` alone since it isn't an ``Exception``.
            await conn.remove_listener(_CHANNEL, _on_notify)
        with contextlib.suppress(Exception):
            await conn.close()


async def start_listener(deliver: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
    """Start the relay: connect, LISTEN, and dispatch other pods' events.

    Reconnects with capped backoff on connection loss so a transient DB
    blip doesn't permanently strand this pod out of the fan-out. Inert (and
    logged once at INFO) when ``DATABASE_URL`` is unset.
    """
    global _listener_task, _drain_task, _outbox  # noqa: PLW0603 — module-level relay state (#804)

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
                await _listen_on(conn, deliver)
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
    _outbox = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    _drain_task = asyncio.create_task(_drain_outbox(_outbox))


# Bounds shutdown (#804 fix 6): without it, a partitioned socket stalls
# `remove_listener`/`close()` with no timeout, and the process's shutdown
# sequence stalls with it, all the way to SIGKILL.
_STOP_TIMEOUT = 5.0


async def stop_listener() -> None:
    """Stop the relay's listener and drain tasks and close its connection."""
    global _connection, _listener_task, _drain_task, _outbox  # noqa: PLW0603 — module-level relay state (#804)

    if _listener_task is not None:
        _listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            # `_listen_on`'s `finally` only ever suppresses plain
            # ``Exception`` around its own cleanup calls (#804 fix 6) — never
            # ``CancelledError``, which isn't an ``Exception`` — so this
            # cancellation cannot be swallowed into `_run`'s reconnect branch
            # and silently replaced with a task that keeps looping forever.
            # `wait_for` bounds the wait itself in case the task still
            # doesn't unwind promptly for some other reason.
            await asyncio.wait_for(_listener_task, timeout=_STOP_TIMEOUT)
        _listener_task = None

    if _drain_task is not None:
        _drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(_drain_task, timeout=_STOP_TIMEOUT)
        _drain_task = None
        _outbox = None

    if _connection is not None:
        with contextlib.suppress(Exception):  # best-effort close on shutdown (#804 fix 6)
            await asyncio.wait_for(_connection.close(), timeout=_STOP_TIMEOUT)
        _connection = None
