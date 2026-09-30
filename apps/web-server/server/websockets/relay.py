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

# The listener keeps its OWN dedicated connection; publishing goes through
# its OWN small connection POOL (#804 round 4 — see below). They used to
# share one connection on the theory that one drain task serialises
# publishing by construction — true for drain-vs-drain, but the listener is a
# SECOND, independent task, and asyncpg does not support two tasks issuing
# operations on the same connection concurrently at all. A reproduction
# against real Postgres hit `InterfaceError: another operation is in
# progress` when a `LISTEN` (issued by the listener after a reconnect) raced
# a queued `execute` (issued by the drain), destroying the whole backlog —
# and the mirror failure at shutdown (the listener's teardown corrupting the
# connection's protocol state while the drain was still using it).
#
# The FIRST fix for this (still round-3-era) gave the drain its own
# hand-rolled connection with its own connect/reconnect/backoff loop,
# duplicating the listener's. That was rejected before landing: getting that
# duplicate wrong risks a NEW form of the round-1 hazard — a dead publish
# connection with no path back, while `is_connected()` (which reads only the
# LISTENER) reports healthy. A small `asyncpg.Pool` (`max_size=2`) gives
# reconnect AND `acquire()`-scoped exclusion for free, is asyncpg's own
# well-tested code instead of ours, and is the smaller diff.
#
# `min_size=0`, NOT `min_size=1` (flagged as a deviation from round 5's
# instruction, not silently changed): `create_pool(..., min_size=1)`
# eagerly opens that one connection before returning, and blocks
# `start_listener` on it — measured directly against an unroutable host,
# `create_pool(min_size=1, ...)` hangs until asyncpg's own connect timeout
# instead of returning. That breaks this module's documented contract that
# `start_listener` never needs Postgres to be up to return (the listener's
# own dedicated connection is already lazy/retrying for the same reason).
# `min_size=0` returns in under 1ms against an unroutable host, no network
# attempted, and the first `acquire()` still connects (and keeps retrying on
# failure) exactly as `min_size=1` would once warm.
_connection: asyncpg.Connection | None = None  # the LISTENER's own connection
_publish_pool: asyncpg.Pool | None = None  # the DRAIN's own connection pool
_listener_task: asyncio.Task[None] | None = None
_drain_task: asyncio.Task[None] | None = None

# Bounds both queues in this module (#804 finding 4 / finding 5). A few
# thousand is plenty for either direction under normal operation — it exists
# to cap MEMORY during an outage (a stuck Postgres, a wedged local client),
# not to throttle a healthy system. 2000 encoded notifications at up to
# ``_MAX_PAYLOAD`` bytes each is a worst case of ~15MB, which is a price
# worth paying to never grow without bound.
_QUEUE_MAXSIZE = 2000

# Bounds shutdown and per-connection teardown (#804 fix 6 / round-3 finding):
# without it, a partitioned socket stalls `remove_listener`/`close()` with no
# timeout, and the process's shutdown sequence stalls with it, all the way to
# SIGKILL. Defined here (not near `stop_listener`) because `_listen_on`'s own
# teardown needs it too.
_STOP_TIMEOUT = 5.0


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


# Dedupes the not-JSON-serializable warning (#804 round 9 finding 5), same
# pattern as this module's other dedupe flags. Reset on the next successful
# encode so a LATER, separate bad payload still warns.
_encode_failure_warned = False


def _encode(kind: str, data: dict[str, Any]) -> str | None:
    """JSON-encode a notification, truncating oversized content to fit.

    Returns ``None`` if the payload still doesn't fit after truncation, OR
    if it isn't JSON-serializable at all — either way the caller drops it
    rather than crash the pipeline over a lost log line.
    """
    global _encode_failure_warned  # noqa: PLW0603 — module-level relay state (#804)

    payload: dict[str, Any] = {"origin": _ORIGIN, "kind": kind, "data": data}
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError):
        # #804 round 9 finding 5: `publish()`'s docstring says "never
        # raises", but this `dumps()` had no guard — `data` is a free-form
        # caller-supplied dict (e.g. `TaskProgress.data`), so the first
        # caller to put a `Path`, a `datetime`, or a `set` in it raised
        # `TypeError` straight out of `publish()`, through
        # `_emit_progress`/`_emit_log`, into the stdout pump — breaking
        # local delivery, the one thing the deliver-then-publish ordering
        # exists to protect.
        if not _encode_failure_warned:
            _encode_failure_warned = True
            logger.warning(
                "[relay] dropping %s notification, not JSON-serializable", kind, exc_info=True
            )
        return None
    _encode_failure_warned = False
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


def _put_outbox_dropping_oldest(outbox: asyncio.Queue[str], item: str) -> None:
    """Enqueue, evicting the OLDEST entry first if the outbox is full.

    #804 round 6 finding 1: matches the inbound queue's drop-OLDEST policy
    (`_put_inbound_dropping_oldest`) — the value of a queued OUTBOUND
    notification decays with age at least as strongly as an inbound one, and
    the previous drop-NEWEST policy had it backwards: during a sustained
    outage it retained the 2000 *oldest* queued events and silently
    discarded every new one, so a pod recovering from the outage flushed
    stale log lines while having permanently lost the most recent ones —
    exactly the case a catching-up pod cares about least.
    """
    global _outbox_full_warned  # noqa: PLW0603 — module-level relay state (#804)

    try:
        outbox.put_nowait(item)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            outbox.get_nowait()
        outbox.put_nowait(item)
        if not _outbox_full_warned:
            _outbox_full_warned = True
            logger.warning("[relay] outbox full (%d), dropping oldest notification", _QUEUE_MAXSIZE)
        return
    _outbox_full_warned = False


async def publish(kind: str, data: dict[str, Any]) -> None:
    """Encode and enqueue an event for other pods. No-op when the relay isn't
    started. Never blocks on Postgres and never raises.

    Never raises: a DB hiccup must not silence a browser attached to the
    pod that emitted the event, which has already been delivered locally
    by the caller before this runs. Beyond that, since #804 finding 4, this
    function no longer talks to Postgres at all — the only ways it can fail
    are `data` not being JSON-serializable (#804 round 9 finding 5 —
    `_encode`'s own `try/except` around `json.dumps`) or the outbox being
    full, both of which it handles by dropping and logging. That means a
    real drain failure (a dead connection, a Postgres hiccup)
    is no longer reported to THIS caller — it never could act on it anyway
    (local delivery already happened) — so the drain task's own logging
    (see `_drain_outbox`) is now the only signal a failure happened at all.

    Bounded no-op window (#804 fix 8): between `start_listener` returning
    and the publish POOL's first successful `acquire()` — and again,
    briefly, whenever the pool's current connection needs replacing — the
    outbox may sit undrained. Unlike before #804 finding 4, publishes during
    that window are now QUEUED, not dropped — the drain task catches up once
    a connection is available — so this window shrank to "relay never
    started at all" (`_drain_task is None`, the check below). `is_connected()`
    above reports the LISTENER's connection specifically (#804 fix 7's
    concern was inbound delivery); the drain has its own, independent
    connection pool since round 3/4 of review. Two things can still lose a
    queued item once it's in: the outbox filling up (drop-OLDEST, see
    `_put_outbox_dropping_oldest`), or one item exhausting the drain's own
    retry budget (see `_drain_outbox`) — both logged.
    """
    outbox = _outbox
    if _drain_task is None or outbox is None:
        return

    encoded = _encode(kind, data)
    if encoded is None:
        logger.warning("[relay] dropping oversized %s notification, could not fit under cap", kind)
        return

    _put_outbox_dropping_oldest(outbox, encoded)


# Dedupes the drain-failure warning — the only warning in this module that
# wasn't already deduped before round 3 of review. Reset on the next
# successful publish so a LATER, separate outage still warns.
_drain_failure_warned = False

# How long a single `pool.acquire()` (which may itself need to (re)connect)
# may take before this item is given up on. Bounds the drain loop against a
# Postgres that's simply unreachable, rather than inheriting asyncpg's own
# (much longer) default connect timeout for every queued item during an
# outage.
_ACQUIRE_TIMEOUT = 5.0

# How long a single `execute()` may take (#804 round 7 finding 2). Bounding
# only `acquire()` was NOT bounding the drain against a Postgres that's
# simply unreachable, as the module used to claim: once the pool holds a
# WARM connection, `acquire()` returns instantly, and it's the unbounded
# `execute()` that then waits — on a half-open socket or a mid-failover
# partition (the case the listener has an explicit `_POLL_INTERVAL` backstop
# for), that's however long TCP retransmission takes (Linux default ~15
# min). During that whole window nothing has raised, so the 3-attempt cap
# never engages, the outbox silently rolls over via drop-oldest, and
# `is_connected()` — which reports the LISTENER's health, not the drain's —
# says the pod is fine.
_EXECUTE_TIMEOUT = 5.0


# Capped backoff for RETRYING one drain item, distinct from
# `_RECONNECT_BACKOFF` (that one paces the LISTENER's own reconnect loop).
# Same values, different purpose: this one paces the drain's retry of the
# item AT THE HEAD of the outbox, not a full reconnect cycle.
_DRAIN_RETRY_BACKOFF = (1, 2, 5, 10, 30)

# Caps how many times ONE item is retried before it's given up on (#804
# round 6 finding 1). Round 5 retried forever, which fixed round 4's silent
# per-item drop but introduced unbounded head-of-line blocking: a single
# PERMANENTLY failing item (a real thing — `CharacterNotInRepertoireError`,
# or "cannot execute NOTIFY during recovery" on a standby) parks the drain
# task on it forever, starving every healthy item queued behind it. 3
# attempts (roughly a 1s + 2s backoff, ~3s total) is enough to ride out a
# TRANSIENT failure (a connection the pool is mid-replacing) without holding
# up the queue for anywhere near as long as a real outage.
_DRAIN_MAX_ATTEMPTS = 3

# Dedupes the drain's give-up warning, same pattern as `_drain_failure_warned`
# (reset on the next successfully drained item). `_drain_dropped_count` is a
# running total, NOT reset — it's a magnitude indicator for whoever reads the
# log, not a per-outage flag.
_drain_drop_warned = False
_drain_dropped_count = 0


async def _drain_outbox(pool: asyncpg.Pool, outbox: asyncio.Queue[str]) -> None:
    """Drain the outbox via a small connection pool dedicated to publishing
    (#804 round 4 — see the module-level comment near `_connection` for why
    this replaced a hand-rolled second connection with its own reconnect
    logic).

    #804 round 6 finding 1: a failed item is retried against the pool with
    capped backoff, up to `_DRAIN_MAX_ATTEMPTS` times, THEN dropped (logged,
    deduped, counted) and the loop moves on to the next queued item. This is
    the middle ground between round 4 (dropped a failed item immediately —
    too lossy: a single transient acquire failure silently ate an event) and
    round 5 (retried forever — too rigid: one PERMANENTLY failing item parks
    the drain task on it forever, starving every healthy item queued behind
    it, which a real reproduction confirmed: 5 healthy items queued behind 1
    poisoned one, 0 published after 1s). The outbox itself (bounded,
    drop-OLDEST on overflow — see `_put_outbox_dropping_oldest`) is still
    what holds items across a genuinely transient reconnect window; this cap
    only bounds how long ONE item may block the ones behind it.
    """
    global _drain_failure_warned, _drain_drop_warned, _drain_dropped_count  # noqa: PLW0603 — module-level relay state (#804)

    while True:
        encoded = await outbox.get()
        attempt = 0
        while True:
            try:
                async with pool.acquire(timeout=_ACQUIRE_TIMEOUT) as conn:
                    await conn.execute(
                        "SELECT pg_notify($1, $2)", _CHANNEL, encoded, timeout=_EXECUTE_TIMEOUT
                    )
            except Exception:  # noqa: BLE001 — retried/dropped below, must not kill the drain task
                attempt += 1
                if attempt >= _DRAIN_MAX_ATTEMPTS:
                    _drain_dropped_count += 1
                    if not _drain_drop_warned:
                        _drain_drop_warned = True
                        logger.warning(
                            "[relay] dropping outbox item after %d failed attempts"
                            " (%d dropped so far)",
                            _DRAIN_MAX_ATTEMPTS,
                            _drain_dropped_count,
                            exc_info=True,
                        )
                    break
                if not _drain_failure_warned:
                    _drain_failure_warned = True
                    logger.warning(
                        "[relay] outbox drain failed, retrying with backoff", exc_info=True
                    )
                delay = _DRAIN_RETRY_BACKOFF[min(attempt - 1, len(_DRAIN_RETRY_BACKOFF) - 1)]
                await asyncio.sleep(delay)
                continue
            _drain_failure_warned = False
            _drain_drop_warned = False
            break


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
            # Bounded (#804 round-3 finding): unbounded, a partitioned
            # socket stalls this exactly as `stop_listener`'s own teardown
            # could.
            await asyncio.wait_for(
                conn.remove_listener(_CHANNEL, _on_notify), timeout=_STOP_TIMEOUT
            )
        with contextlib.suppress(Exception):
            await asyncio.wait_for(conn.close(), timeout=_STOP_TIMEOUT)


async def _run_listener(
    url: str, deliver: Callable[[str, dict[str, Any]], Awaitable[None]]
) -> None:
    """Connect, `_listen_on`, and reconnect with capped backoff for as long
    as the relay is started — the listener's half of #804 round 3's two
    independent connections."""
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


async def start_listener(deliver: Callable[[str, dict[str, Any]], Awaitable[None]]) -> None:
    """Start the relay: the listener's own dedicated connection, plus a
    small pool dedicated to publishing (#804 round 4 — see the module-level
    comment near ``_connection``). Inert (and logged once at INFO) when
    ``DATABASE_URL`` is unset.
    """
    global _listener_task, _drain_task, _outbox, _publish_pool  # noqa: PLW0603 — module-level relay state (#804)

    url = _resolve_asyncpg_url()
    if url is None:
        logger.info("[relay] DATABASE_URL not set, WebSocket fan-out relay is inert")
        return

    if _listener_task is not None:
        # #804 round 5 finding 6: without this guard, a second call
        # overwrites `_outbox`/`_drain_task`/`_publish_pool` while the OLD
        # listener and drain tasks keep running — the drain leaks, still
        # awaiting the queue nobody publishes to anymore, and (pre-pool) this
        # was finding 1 all over again; with a pool, the leak is a second
        # pool instead of a second raw connection, but it is still a leak.
        # Not reachable from `lifespan` today, but reachable from tests and
        # any future restart path.
        logger.warning("[relay] start_listener called while already running, ignoring")
        return

    # Claim the guard SYNCHRONOUSLY, before the first `await` below (#804
    # round 6 finding E). The check above alone is not atomic against a
    # SECOND concurrent call: two calls could both pass it before either
    # reaches an `await` — asyncio only switches tasks AT an await point —
    # and each would then create its own pool/listener, with `stop_listener`
    # only ever closing one of them. `asyncio.create_task` schedules but
    # does not itself await, so setting `_listener_task` to its result here,
    # with no `await` between the check and this line, closes that window:
    # a second call's own check now sees a non-`None` `_listener_task` and
    # bails out before it can start anything.
    _listener_task = asyncio.create_task(_run_listener(url, deliver))

    outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    try:
        # See the module-level comment near `_connection` for why this is
        # `min_size=0`, not `min_size=1`.
        pool = await asyncpg.create_pool(url, min_size=0, max_size=2)
    except Exception:
        # #804 round 9 finding 1: `start_listener` must fully start or fully
        # fail — my own round-6 re-entry-guard fix claimed `_listener_task`
        # BEFORE this await to close a concurrent-call race, but left THIS
        # window open: if `create_pool` itself raises (Postgres not yet
        # accepting connections during a rolling deploy — the exact case
        # `main.py`'s own startup catch anticipates), `_listener_task` stayed
        # claimed forever while `_drain_task`/`_outbox` were never assigned.
        # That pod's inbound listener still works (it retries and connects
        # fine on its own), but `publish()` no-ops forever at its
        # `_drain_task is None` guard — the pod relays NOTHING outbound for
        # its entire lifetime — and the re-entry guard above refuses every
        # retry since `_listener_task` is still set. Undo the claim before
        # propagating, so the caller (`main.py`'s own catch) gets a pod that
        # can actually retry, not one that's silently, permanently half-alive.
        _listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(_listener_task, timeout=_LISTENER_SHUTDOWN_TIMEOUT)
        _listener_task = None
        raise

    _outbox = outbox
    _publish_pool = pool
    _drain_task = asyncio.create_task(_drain_outbox(pool, outbox))


# Headroom over `_STOP_TIMEOUT` for AWAITING the listener task specifically
# (#804 round 4): `_listen_on`'s own `finally` bounds `remove_listener` AND
# `close()` each by `_STOP_TIMEOUT`, run one after the other, so the task can
# legitimately take close to 2x`_STOP_TIMEOUT` to unwind after cancellation.
# Awaiting it here with only `_STOP_TIMEOUT` would give up mid-cleanup and
# abandon a task that is still correctly, boundedly finishing — not hung,
# just not yet done. This is "bound the wait", not "bound the stall"; the
# STALL is what the two inner timeouts already bound.
_LISTENER_SHUTDOWN_TIMEOUT = _STOP_TIMEOUT * 3


async def stop_listener() -> None:
    """Stop the relay's drain and listener tasks and close both.

    Order matters (#804 round-3 finding): the DRAIN is cancelled FIRST, and
    fully awaited, before the listener's teardown even begins. Stop
    producing before tearing down whichever connections are involved — with
    the drain's own connection pool since round 4, a wrong order can no
    longer corrupt the LISTENER's protocol state the way it could when they
    shared one connection, but "the producer keeps running while its target
    tears down" is the wrong shape regardless, and review asked for the
    order explicitly, so it's pinned by a test, not just left to accident.
    """
    global _connection, _publish_pool, _listener_task, _drain_task, _outbox  # noqa: PLW0603 — module-level relay state (#804)

    if _drain_task is not None:
        _drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            # Nothing inside `_drain_outbox` has its own unbounded cleanup
            # (unlike the listener's `_listen_on`) — cancelling it interrupts
            # `outbox.get()` or a single `pool.acquire()`/`execute()` and
            # nothing else, so `_STOP_TIMEOUT` alone is enough headroom here.
            await asyncio.wait_for(_drain_task, timeout=_STOP_TIMEOUT)
        _drain_task = None
        _outbox = None

    if _publish_pool is not None:
        # `Pool.close()` is graceful (waits for in-flight queries) and
        # bounded here; `Pool.terminate()` is synchronous and immediate —
        # used as a hard fallback so a pool that won't close gracefully
        # within the timeout still cannot outlive `stop_listener()` (#804
        # round 4: "bounding the wait is not bounding the stall").
        try:
            await asyncio.wait_for(_publish_pool.close(), timeout=_STOP_TIMEOUT)
        except Exception:  # noqa: BLE001 — best-effort close on shutdown (#804 fix 6)
            _publish_pool.terminate()
        _publish_pool = None

    if _listener_task is not None:
        _listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            # `_listen_on`'s `finally` only ever suppresses plain
            # ``Exception`` around its own cleanup calls (#804 fix 6) — never
            # ``CancelledError``, which isn't an ``Exception`` — so this
            # cancellation cannot be swallowed into `_run_listener`'s
            # reconnect branch and silently replaced with a task that keeps
            # looping forever. See `_LISTENER_SHUTDOWN_TIMEOUT` above for why
            # the bound here is wider than `_STOP_TIMEOUT`.
            await asyncio.wait_for(_listener_task, timeout=_LISTENER_SHUTDOWN_TIMEOUT)
        _listener_task = None

    # `_run_listener`'s own `finally` already resets `_connection` on any
    # normal exit (cancellation included) once `_listen_on` returns — this
    # is the defensive fallback for a connection that got set without its
    # owning task completing cleanly within `_LISTENER_SHUTDOWN_TIMEOUT`.
    if _connection is not None:
        with contextlib.suppress(Exception):  # best-effort close on shutdown (#804 fix 6)
            await asyncio.wait_for(_connection.close(), timeout=_STOP_TIMEOUT)
        _connection = None
