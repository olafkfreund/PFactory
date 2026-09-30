"""Cross-pod WebSocket fan-out via Postgres LISTEN/NOTIFY (#804).

Every test names the production-code mutation it must fail against, in its
docstring, and that claim was verified by hand: apply the mutation, run the
test, watch it fail, revert (see the report accompanying this file for the
per-test outcome). These are in-process unit tests against ``relay.py`` /
``_dispatch.py`` / ``events.py`` with a fake asyncpg connection — no real
Postgres. The genuinely cross-process case (two real origins on one shared
database) is ``tests/postgres/test_ws_relay_postgres.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from server.services.agent_service import TaskLog, TaskPhase, TaskProgress, get_agent_service
from server.websockets import _dispatch, events, relay


@pytest.fixture(autouse=True)
async def _clean_relay_state():
    """Reset the relay's process-global state around every test.

    ``relay.py`` is a singleton module (one connection, one listener task
    shared by the whole process) — without this, a test that starts a fake
    listener would leak a running task into the next test. The three
    dedup-warning flags are reset too: each is a plain module-level bool/set,
    not touched by `stop_listener()`, so a test that trips one would
    otherwise leave it tripped for whichever test happens to run next.
    """
    yield
    await relay.stop_listener()
    # NOT `relay._connection = None` here: `stop_listener()` already clears
    # it unconditionally (either via `_run_listener`'s own `finally`, or its
    # own defensive fallback) — checked while investigating whether this line
    # still did anything (#804 finding H); it didn't, so it's gone.
    relay._publish_pool = None
    relay._outbox_full_warned = False
    relay._inbound_full_warned = False
    relay._drain_failure_warned = False


class _FakeConnection:
    """Stands in for ``asyncpg.Connection``.

    Delivers its canned notifications the instant ``add_listener`` is
    awaited, mimicking a NOTIFY arriving right after LISTEN registers.
    Termination is simulated on demand via ``simulate_termination()``, which
    flips ``is_closed()`` to ``True`` and, unless told not to, also invokes
    whatever callback was registered with ``add_termination_listener`` —
    the two independent paths relay.py's reconnect logic must survive
    (#804 fix 1): the callback firing (the common case), and the callback
    NOT firing (a half-open socket / network partition) while ``is_closed()``
    still flips true, which is what a real reproduction against Postgres
    (`pg_terminate_backend`) found happens the instant a connection dies.
    """

    def __init__(self, notifications: list[tuple[str, str]]) -> None:
        self._notifications = notifications
        self.closed = False
        self._termination_callback: Any = None
        self.calls: list[str] = []

    async def add_listener(self, _channel: str, callback: Any) -> None:
        for channel, payload in self._notifications:
            callback(self, 0, channel, payload)

    async def remove_listener(self, _channel: str, _callback: Any) -> None:
        pass

    def add_termination_listener(self, callback: Any) -> None:
        self._termination_callback = callback

    def remove_termination_listener(self, _callback: Any) -> None:
        self._termination_callback = None

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True

    async def execute(self, _sql: str, _channel: str, payload: str, **_kwargs: Any) -> None:
        self.calls.append(payload)

    def simulate_termination(self, *, fire_callback: bool = True) -> None:
        """Mark the connection dead, as a real connection loss would.

        ``fire_callback=False`` simulates the termination LISTENER never
        firing — the case the polled ``is_closed()`` backstop exists for.
        """
        self.closed = True
        if fire_callback and self._termination_callback is not None:
            self._termination_callback(self)


class _FakeWebSocket:
    """Stands in for a connected client: records what it was sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


async def _wait_until(predicate: Any, deadline_seconds: float = 2.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_seconds
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.01)


def _connect_sequence(*conns: Any) -> AsyncMock:
    """An ``asyncpg.connect`` replacement returning each of ``conns`` in
    order, then an endless supply of fresh, do-nothing connections.

    Since #804 round 3, the listener and the drain each call
    ``asyncpg.connect`` independently — a plain fixed-length ``side_effect``
    list runs out (``StopAsyncIteration``) the moment BOTH tasks have
    connected across a test's lifetime. This lets a test pin exactly the
    calls it cares about, by position, without needing to account for
    however many additional calls the OTHER task makes.
    """
    remaining = list(conns)

    def _connect(*_args: Any, **_kwargs: Any) -> Any:
        if remaining:
            return remaining.pop(0)
        return _FakeConnection([])

    return AsyncMock(side_effect=_connect)


class _FakeAcquireContext:
    """What ``asyncpg.Pool.acquire()`` returns: an async context manager
    yielding one connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        pass


class _FakePool:
    """Stands in for ``asyncpg.Pool`` (#804 round 4 — the drain's connection
    pool). ``acquire()`` hands out each of ``conns`` in order, then an
    endless supply of fresh, do-nothing connections — same reasoning as
    ``_connect_sequence`` above, one level up the stack.
    """

    def __init__(self, *conns: Any) -> None:
        self._conns = list(conns)
        self.closed = False
        self.terminated = False

    def acquire(self, *, timeout: float | None = None) -> _FakeAcquireContext:  # noqa: ARG002 — matches asyncpg.Pool.acquire's signature
        conn = self._conns.pop(0) if self._conns else _FakeConnection([])
        return _FakeAcquireContext(conn)

    async def close(self) -> None:
        self.closed = True

    def terminate(self) -> None:
        self.terminated = True


# ── 1. publish is a no-op without a started relay ──────────────────────────


async def test_publish_without_a_drain_task_is_noop_and_does_not_raise(monkeypatch) -> None:
    """#804 round 5: `publish()`'s guard reads `_drain_task`/`_outbox`, not
    `_connection` (a leftover from before the outbox existed — `_connection`
    is the LISTENER's, and `publish` never touched it). Retargeted at the
    guard that actually exists.

    MUTATION: narrow `publish()`'s guard from `if _drain_task is None or
    outbox is None: return` to `if outbox is None: return` ⇒ this fails —
    with the drain task gone but a stale `_outbox` left set (a crashed drain
    that was never cleaned up), `_encode` would get called and the
    notification would be enqueued into a queue nobody is draining, instead
    of being dropped as a clean no-op.
    """
    monkeypatch.setattr(relay, "_drain_task", None)
    monkeypatch.setattr(relay, "_outbox", asyncio.Queue())
    encode_spy = Mock(wraps=relay._encode)
    monkeypatch.setattr(relay, "_encode", encode_spy)

    await relay.publish("task:log", {"task_id": "t1", "content": "hi"})  # must not raise

    encode_spy.assert_not_called()


# ── 2 & 3. origin filtering on the listener side ────────────────────────────


async def test_listener_skips_own_origin_but_delivers_others(monkeypatch) -> None:
    """MUTATION: delete `if message.get("origin") == _ORIGIN: continue` ⇒ this
    fails — the own-origin notification would show up in `delivered` too.

    Both notifications are queued before the listener starts processing, so
    if the own-origin one is (wrongly) delivered, it lands FIRST — this
    isn't a timing guess.
    """
    own_payload = json.dumps(
        {"origin": relay._ORIGIN, "kind": "task:log", "data": {"marker": "own"}}
    )
    other_payload = json.dumps(
        {"origin": "other-pod:deadbeef", "kind": "task:log", "data": {"marker": "other"}}
    )
    fake_conn = _FakeConnection([("pfactory_ws", own_payload), ("pfactory_ws", other_payload)])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=fake_conn))

    delivered: list[tuple[str, dict]] = []

    async def deliver(kind: str, data: dict) -> None:
        delivered.append((kind, data))

    await relay.start_listener(deliver)
    await _wait_until(lambda: len(delivered) >= 1)

    assert delivered == [("task:log", {"marker": "other"})]


async def test_listener_delivers_foreign_origin_with_decoded_payload(monkeypatch) -> None:
    """MUTATION: pass the raw JSON string to `deliver` instead of the decoded
    `message.get("data", {})` ⇒ this fails (delivered payload wouldn't be a dict
    with the expected keys)."""
    payload = json.dumps(
        {
            "origin": "other-pod:deadbeef",
            "kind": "ws:broadcast",
            "data": {"event_type": "task:status", "payload": {"taskId": "t1"}},
        }
    )
    fake_conn = _FakeConnection([("pfactory_ws", payload)])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=fake_conn))

    delivered: list[tuple[str, dict]] = []

    async def deliver(kind: str, data: dict) -> None:
        delivered.append((kind, data))

    await relay.start_listener(deliver)
    await _wait_until(lambda: len(delivered) >= 1)

    assert delivered == [
        ("ws:broadcast", {"event_type": "task:status", "payload": {"taskId": "t1"}})
    ]


# ── 4 & 5. _encode truncation ───────────────────────────────────────────────


def test_encode_truncates_oversized_content_and_stays_under_cap() -> None:
    """MUTATION: delete `payload["truncated"] = True` in _encode() ⇒ this
    fails — the decoded envelope would fit under the cap but lack the flag.

    The flag lives on the ENVELOPE, not inside ``data`` (#804 fix 2) — see
    ``test_dispatch_task_log_rebuilds_after_truncation`` for why: putting it
    inside ``data`` broke ``TaskLog(**data)`` on the receiving pod.

    Assertions are LITERAL numbers computed once against this exact input,
    not `relay._MAX_PAYLOAD` recomputed — a mutation that truncates every
    payload to `content[:1]`, or that shrinks `_MAX_PAYLOAD` itself to 100,
    both still satisfy "some content, under whatever the cap now is"; they
    do not satisfy "7791 characters retained out of 20000, total envelope
    7900 bytes".
    """
    data = {"task_id": "t1", "content": "x" * 20_000}

    encoded = relay._encode("task:log", data)

    assert encoded is not None
    assert len(encoded.encode()) == 7900
    decoded = json.loads(encoded)
    assert decoded["truncated"] is True
    assert "truncated" not in decoded["data"]
    assert decoded["data"]["content"] == "x" * 7791


def test_encode_returns_none_when_it_cannot_fit_under_cap() -> None:
    """MUTATION: change the final `return None` to `return encoded` (the
    still-oversized attempt) ⇒ this fails — the result would exceed the cap.

    Bloating a sibling field (not `content`) forces this: _encode only
    truncates `content`, so once the REST of the envelope alone exceeds the
    cap, no amount of content-shrinking can rescue it.
    """
    data = {"task_id": "t1", "content": "short", "extra": "y" * 20_000}

    encoded = relay._encode("task:log", data)

    assert encoded is None


# ── 6. a publish failure must not block local delivery ─────────────────────


async def test_publish_failure_does_not_prevent_local_delivery_or_propagate(
    monkeypatch,
) -> None:
    """Local delivery must complete even when the relay side of a
    `broadcast_event` call hits a full outbox — which, since drop-oldest
    landed (#804 round 6 finding 1), is a deliberate capacity policy, not a
    failure `publish()` catches: `publish()` itself has no `try/except` at
    all; the drop-and-warn handling this test exercises lives in
    `_put_outbox_dropping_oldest`. This test's actual claim is narrower and
    still worth pinning: an outbox already at capacity must not stop
    `broadcast_event`'s local half from running, or raise out of it.

    Since #804 finding 4, `publish` no longer talks to Postgres directly (see
    the finding-4 tests below) — a full outbox is the only way enqueueing
    can be observably eventful at all, so that's what this test drives.
    """
    ws = _FakeWebSocket()
    events._register_client(ws, None)
    # A real (if inert) Task, not a bare sentinel object: `_clean_relay_state`'s
    # teardown calls `stop_listener()` unconditionally, which calls
    # `.cancel()` on whatever `_drain_task` currently is.
    fake_drain_task = asyncio.create_task(asyncio.sleep(3600))
    monkeypatch.setattr(relay, "_drain_task", fake_drain_task)
    full_outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    full_outbox.put_nowait("occupying the only slot")
    monkeypatch.setattr(relay, "_outbox", full_outbox)
    try:
        await events.broadcast_event("task:test", {"x": 1})  # must not raise
    finally:
        events._unregister_client(ws)
        fake_drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_drain_task

    assert ws.sent == [json.dumps({"type": "task:test", "payload": {"x": 1}})]


# ── 7. review finding 4: publish must not block on Postgres, and the outbox ──
# ── that replaces it must be bounded and drain-failure-tolerant ────────────


class _MinimalConnection:
    """Everything `_listen_on` needs from a connection besides `execute`,
    shared by the finding-4/finding-5 fakes below so each only has to define
    its own failure mode."""

    def __init__(self) -> None:
        self.closed = False

    async def add_listener(self, _channel: str, _callback: Any) -> None:
        pass

    async def remove_listener(self, _channel: str, _callback: Any) -> None:
        pass

    def add_termination_listener(self, _callback: Any) -> None:
        pass

    def remove_termination_listener(self, _callback: Any) -> None:
        pass

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class _SlowExecuteConnection(_MinimalConnection):
    """A connection whose ``execute`` takes far longer than any publish call
    should ever wait for."""

    def __init__(self, sleep_seconds: float) -> None:
        super().__init__()
        self._sleep_seconds = sleep_seconds
        self.calls: list[str] = []

    async def execute(self, _sql: str, _channel: str, payload: str, **_kwargs: Any) -> None:
        await asyncio.sleep(self._sleep_seconds)
        self.calls.append(payload)


async def test_publish_returns_promptly_when_the_db_is_slow(monkeypatch) -> None:
    """MUTATION: revert `publish()` to `await _connection.execute(...)`
    directly (finding 4's original hazard — every agent stdout line paying
    for a Postgres round-trip, serialised process-wide, inside the stdout
    reader) ⇒ this fails — `publish` would block for the full slow-DB
    duration instead of just enqueueing and returning immediately.
    """
    drain_conn = _SlowExecuteConnection(sleep_seconds=2.0)
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(_FakeConnection([])))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool(drain_conn)))

    async def deliver(kind: str, data: dict) -> None:
        pass

    # `create_pool` is awaited directly inside `start_listener` (#804 round
    # 4), so the pool is ready the instant this returns — no `_wait_until`
    # needed the way the LISTENER's own background connect still needs one.
    await relay.start_listener(deliver)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await relay.publish("task:log", {"task_id": "t1", "content": "hi"})
    elapsed = loop.time() - start

    assert elapsed < 0.5, f"publish() took {elapsed}s — it's blocking on the DB again"


class _HangingExecuteConnection(_MinimalConnection):
    """A connection whose ``execute`` never returns — keeps the drain task
    permanently busy on its first item, so later ones back up in the
    outbox."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def execute(self, _sql: str, _channel: str, _payload: str, **_kwargs: Any) -> None:
        await asyncio.sleep(3600)


async def test_publish_drops_and_warns_once_when_outbox_is_full(monkeypatch, caplog) -> None:
    """MUTATION: create the outbox with a bare `asyncio.Queue()` (no
    `maxsize`) instead of `maxsize=_QUEUE_MAXSIZE` ⇒ this fails — `put_nowait`
    never raises `QueueFull` no matter how far behind the drain task falls,
    so nothing is ever dropped or warned about, and the outbox grows without
    bound during an outage.
    """
    monkeypatch.setattr(relay, "_QUEUE_MAXSIZE", 3)
    drain_conn = _HangingExecuteConnection()
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(_FakeConnection([])))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool(drain_conn)))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)

    with caplog.at_level(logging.WARNING, logger=relay.__name__):
        for i in range(10):
            await relay.publish("task:log", {"task_id": "t1", "content": f"x{i}"})  # must not raise

    matches = [r for r in caplog.records if "outbox full" in r.getMessage()]
    assert len(matches) == 1


async def test_outbox_drops_oldest_so_the_freshest_notification_survives() -> None:
    """#804 round 6 finding 1: the outbox now matches the inbound queue's
    drop-OLDEST overflow policy — previously it dropped-newest, which meant a
    sustained outage retained the 2000 *oldest* queued events and silently
    discarded every new one, so a pod recovering from the outage flushed
    stale log lines while having permanently lost the most recent ones.

    MUTATION: revert `_put_outbox_dropping_oldest` to drop-NEWEST (reject a
    new arrival outright once the queue is full, rather than evicting the
    head to admit it) ⇒ this fails — the queue would retain items 0-2
    instead of the freshest 7-9.
    """
    outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=3)
    for i in range(10):
        relay._put_outbox_dropping_oldest(outbox, str(i))

    remaining = []
    while not outbox.empty():
        remaining.append(outbox.get_nowait())
    assert remaining == ["7", "8", "9"]


class _DropsOnConnectionConnection(_MinimalConnection):
    """Every ``execute`` raises — models one failed publish attempt.

    #804 round 5 finding 5: a failed item is now retried IN PLACE with
    capped backoff, not dropped-and-moved-past — see `_drain_outbox`'s
    docstring for why.
    """

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom: connection dropped")


class _AlwaysDropsConnection(_MinimalConnection):
    """Every ``execute`` raises — models an item that fails PERMANENTLY (a
    real thing: `CharacterNotInRepertoireError`, or "cannot execute NOTIFY
    during recovery" on a standby), as opposed to `_DropsOnConnectionConnection`
    used elsewhere for a transient, eventually-recovering failure."""

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("boom: permanently poisoned item")


async def test_drain_gives_up_after_max_attempts_so_the_next_item_is_not_starved(
    monkeypatch,
) -> None:
    """#804 round 6 finding 1: round 5's retry-forever fixed round 4's
    silent per-item drop but introduced unbounded head-of-line blocking — a
    reproduction confirmed 5 healthy items queued behind 1 permanently
    failing one, 0 published after 1s. The fix is a middle ground: retry up
    to `_DRAIN_MAX_ATTEMPTS` times, then give up on THAT item and move on.

    MUTATION: remove the `if attempt >= _DRAIN_MAX_ATTEMPTS: ... break`
    give-up path (retry forever again) ⇒ this fails — the second, healthy
    item is never drained because the first, permanently-poisoned one is
    never given up on.
    """
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (0,))  # keep the test fast
    poisoned = _AlwaysDropsConnection()
    healthy = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(_FakeConnection([])))
    monkeypatch.setattr(
        relay.asyncpg,
        "create_pool",
        # `_DRAIN_MAX_ATTEMPTS` (3) failing acquires for the FIRST item, then
        # the second item's own acquire lands on the healthy connection.
        AsyncMock(return_value=_FakePool(poisoned, poisoned, poisoned, healthy)),
    )

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)

    await relay.publish("task:log", {"task_id": "t1", "content": "permanently poisoned"})
    await relay.publish("task:log", {"task_id": "t1", "content": "must not be starved"})
    await _wait_until(lambda: len(healthy.calls) >= 1, deadline_seconds=3.0)

    assert len(healthy.calls) == 1
    assert "must not be starved" in healthy.calls[0]


async def test_drain_drop_warning_is_deduped_and_counted(monkeypatch, caplog) -> None:
    """#804 round 6 finding 1: the give-up-and-drop path logs once per
    outage (deduped, like its siblings) but tracks a running drop count so
    the log line still conveys the outage's magnitude.

    MUTATION: remove the `_drain_drop_warned` dedupe (warn on every dropped
    item) ⇒ this fails — two permanently-poisoned items would each log their
    own "dropping outbox item" warning instead of just the first.
    """
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (0,))
    relay._drain_drop_warned = False
    starting_count = relay._drain_dropped_count
    poisoned_a = _AlwaysDropsConnection()
    poisoned_b = _AlwaysDropsConnection()
    healthy = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(_FakeConnection([])))
    monkeypatch.setattr(
        relay.asyncpg,
        "create_pool",
        AsyncMock(
            return_value=_FakePool(
                poisoned_a, poisoned_a, poisoned_a, poisoned_b, poisoned_b, poisoned_b, healthy
            )
        ),
    )

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)

    with caplog.at_level(logging.WARNING, logger=relay.__name__):
        await relay.publish("task:log", {"task_id": "t1", "content": "first poisoned item"})
        await relay.publish("task:log", {"task_id": "t1", "content": "second poisoned item"})
        await relay.publish("task:log", {"task_id": "t1", "content": "third, healthy item"})
        await _wait_until(lambda: len(healthy.calls) >= 1, deadline_seconds=3.0)

    matches = [r for r in caplog.records if "dropping outbox item" in r.getMessage()]
    assert len(matches) == 1
    assert relay._drain_dropped_count == starting_count + 2


async def test_drain_retries_a_failed_item_in_place_until_it_succeeds(monkeypatch) -> None:
    """#804 round 5 finding 5: a failed item is retried in place, not
    dropped-and-moved-past — so a SECOND, already-queued item must not reach
    the pool before the first one finally succeeds.

    MUTATION: revert `_drain_outbox`'s retry loop to round 4's
    drop-and-continue (catch, log, and `continue` the OUTER loop to pull the
    NEXT queued item instead of retrying this one) ⇒ this fails — the second
    item would land on the dying connection's next scripted `acquire()`
    (consuming the slot this test reserved for the first item's second
    retry) before the healthy connection is ever reached, and the first
    item's own content would never land anywhere.
    """
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (0,))  # keep the test fast
    dying = _DropsOnConnectionConnection()
    healthy = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(_FakeConnection([])))
    monkeypatch.setattr(
        relay.asyncpg,
        "create_pool",
        # Two failing acquires (the first attempt, then one retry) before the
        # third acquire — still for the SAME first item — finally succeeds.
        AsyncMock(return_value=_FakePool(dying, dying, healthy)),
    )

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)

    await relay.publish("task:log", {"task_id": "t1", "content": "first, retried twice"})
    await relay.publish("task:log", {"task_id": "t1", "content": "second, must wait its turn"})
    await _wait_until(lambda: len(healthy.calls) >= 1, deadline_seconds=3.0)

    assert len(healthy.calls) == 1
    assert "first, retried twice" in healthy.calls[0]


# ── 8 & 9. dispatch: unknown kinds and raising handlers never break the loop ─


async def test_dispatch_unknown_kind_warns_once_across_repeated_calls(caplog) -> None:
    """MUTATION: remove the `_warned_unknown` dedupe (warn every call) ⇒ this
    fails — three calls would produce three warnings, not one."""
    _dispatch._warned_unknown.clear()

    with caplog.at_level(logging.WARNING, logger=_dispatch.__name__):
        for _ in range(3):
            await _dispatch.dispatch("nonexistent:kind", {})  # must never raise

    matches = [r for r in caplog.records if "unknown kind" in r.getMessage()]
    assert len(matches) == 1


async def test_dispatch_swallows_a_raising_handler(monkeypatch) -> None:
    """MUTATION: remove dispatch()'s try/except around `await handler(data)` ⇒
    this fails — the handler's RuntimeError would propagate out of dispatch()."""

    async def _boom(_data: dict) -> None:
        raise RuntimeError("boom: bad payload")

    monkeypatch.setitem(_dispatch._HANDLERS, "task:log", _boom)

    await _dispatch.dispatch("task:log", {"task_id": "t1"})  # must not raise


# ── 10. task:progress round-trip rebuilds the TaskPhase enum ───────────────


async def test_dispatch_task_progress_rebuilds_phase_as_enum(monkeypatch) -> None:
    """MUTATION: in _dispatch_task_progress, pass `data` straight through
    (skip rebuilding `data["phase"]` as a `TaskPhase`) ⇒ this fails — `.phase`
    would stay the plain string that survived the JSON round-trip, and
    `.value` (what `websockets/progress.py` reads) would raise AttributeError.

    Built from a real `TaskProgress` via `dataclasses.asdict` and a genuine
    `json.dumps`/`json.loads` round-trip, not a hand-maintained field list —
    if `TaskProgress` ever gains, loses or renames a field, or ever collides
    with a key `_encode` adds (like `truncated`, fix 2), THIS test's own
    construction breaks first and loudly, rather than silently drifting out
    of sync with the real dataclass.
    """
    captured: list[Any] = []

    async def _fake_deliver_local_progress(progress: Any) -> None:
        captured.append(progress)

    monkeypatch.setattr(
        get_agent_service(), "_deliver_local_progress", _fake_deliver_local_progress
    )

    progress = TaskProgress(task_id="t1", phase=TaskPhase.CODING, message="hi")
    data = json.loads(json.dumps(asdict(progress)))
    assert isinstance(data["phase"], str)  # confirms the round-trip actually degraded it

    await _dispatch.dispatch("task:progress", data)

    assert len(captured) == 1
    assert captured[0].phase.value == "coding"


# ── 11. review fix 1: a dead connection must reach the reconnect path ───────


async def test_listener_reconnects_when_termination_fires(monkeypatch) -> None:
    """The fast path: asyncpg invokes the termination callback, which puts the
    sentinel on the queue immediately, so reconnect doesn't have to wait for
    the poll backstop's `_POLL_INTERVAL`.

    Not a mutation-isolating test on its own — deleting the termination
    listener here would just make THIS test slower (falling back to the
    backstop) rather than fail outright, which is why the deadline below is
    tuned tight for the fast path specifically.
    `test_listener_reconnects_when_termination_never_fires` is the one that
    isolates the backstop and is sensitive to removing it.
    """
    conn1 = _FakeConnection([])
    conn2 = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    # The drain no longer calls `connect` at all (#804 round 4: it uses a
    # pool) so this sequence only has to cover the LISTENER's two connects.
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(conn1, conn2))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool()))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is conn1)

    conn1.simulate_termination()  # fire_callback=True (default) — the fast path

    # The sentinel wakes the loop immediately; only `_RECONNECT_BACKOFF[0]`
    # (1s) stands between termination and the retry.
    await _wait_until(lambda: relay._connection is conn2, deadline_seconds=3.0)


async def test_listener_reconnects_when_termination_never_fires(monkeypatch) -> None:
    """The guarantee: even if the termination callback never fires at all —
    a half-open socket, a network partition where no FIN arrives, or some
    asyncpg path that simply doesn't invoke it — the loop must still notice
    the connection died and reconnect. This is the case an independent
    review's `pg_terminate_backend` reproduction actually hit, and the one
    the callback alone cannot cover by construction: the thing that would
    wake the loop is the same thing that failed.

    MUTATION: unwrap `asyncio.wait_for(queue.get(), timeout=_POLL_INTERVAL)`
    back to a bare `await queue.get()` in `_listen_on()` ⇒ this fails — with
    the callback never firing AND nothing ever timing out the wait, nothing
    wakes the loop at all, and `asyncpg.connect` is never called a second
    time (bounded by this test's own deadline, not a real hang).
    """
    conn1 = _FakeConnection([])
    conn2 = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(conn1, conn2))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool()))
    monkeypatch.setattr(relay, "_POLL_INTERVAL", 0.3)  # keep the test fast

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is conn1)

    conn1.simulate_termination(fire_callback=False)  # the callback never fires

    # Reconnect now depends entirely on the poll backstop noticing
    # `is_closed()` on a `wait_for` timeout, plus `_RECONNECT_BACKOFF[0]`
    # (1s) before the retry.
    await _wait_until(lambda: relay._connection is conn2, deadline_seconds=5.0)


async def test_listener_actually_reconnects_after_a_lost_connection(monkeypatch) -> None:
    """Verifies the outer reconnect loop + backoff exist at all, independent
    of which liveness signal (sentinel vs. poll) noticed the connection died.

    MUTATION: delete `_run`'s outer `while True:`/backoff wrapper entirely
    (connect once, run `_listen_on` once, done) ⇒ this fails — `asyncpg.connect`
    is never called a second time no matter how the first connection dies.
    Already proven as a side effect by both termination tests above (whose
    own mutation each independently make this fail too); this test exists so
    that fact is asserted explicitly rather than left implicit.
    """
    conn1 = _FakeConnection([])
    conn2 = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(conn1, conn2))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool()))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is conn1)

    conn1.simulate_termination()

    await _wait_until(lambda: relay._connection is conn2, deadline_seconds=3.0)


# ── 12. review fix 2: the truncation flag must not break the rebuild ───────


async def test_dispatch_task_log_rebuilds_after_truncation(monkeypatch) -> None:
    """MUTATION: put `truncated` back inside `data` (e.g. set
    `target_data["truncated"] = True` instead of `payload["truncated"] = True`
    in _encode()) ⇒ this fails — `TaskLog(**data)` raises `TypeError` (`TaskLog`
    has exactly 5 fields, none of them `truncated`), `dispatch` swallows it, and
    the oversized log line is DROPPED cross-pod instead of arriving truncated —
    the precise case truncation exists for.
    """
    encoded = relay._encode("task:log", {"task_id": "t1", "content": "x" * 20_000})
    assert encoded is not None
    envelope = json.loads(encoded)
    assert envelope["truncated"] is True

    captured: list[Any] = []

    async def _fake_deliver_local_log(log: Any) -> None:
        captured.append(log)

    monkeypatch.setattr(get_agent_service(), "_deliver_local_log", _fake_deliver_local_log)

    await _dispatch.dispatch("task:log", envelope["data"])  # must not raise / swallow

    assert len(captured) == 1
    assert isinstance(captured[0], TaskLog)
    assert len(captured[0].content) < 20_000


async def test_dispatch_task_log_does_not_republish(monkeypatch) -> None:
    """MUTATION: in `_dispatch_task_log`, call
    `get_agent_service()._emit_log(TaskLog(**data))` instead of
    `._deliver_local_log(...)` ⇒ this fails — `_emit_log` is the PUBLIC path
    that delivers locally AND calls `relay.publish(...)` again. On a real
    two-pod setup that is an infinite ping-pong: pod B receives A's log over
    the wire, dispatch hands it to the public emit path, which re-publishes
    it, and pod A (or a third pod) receives it right back.
    """
    publish_spy = AsyncMock()
    monkeypatch.setattr(relay, "publish", publish_spy)

    delivered: list[Any] = []

    async def _fake_deliver_local_log(log: Any) -> None:
        delivered.append(log)

    monkeypatch.setattr(get_agent_service(), "_deliver_local_log", _fake_deliver_local_log)

    data = asdict(TaskLog(task_id="t1", content="hi"))
    await _dispatch.dispatch("task:log", data)

    assert len(delivered) == 1
    publish_spy.assert_not_awaited()


# ── 13. review fix 3: the nested ws:broadcast chunk shape must truncate ─────


def test_encode_truncates_nested_ws_broadcast_chunk_content() -> None:
    """MUTATION: revert `_locate_truncatable_content` to look for `data["chunk"]`
    instead of `data["payload"]["chunk"]` ⇒ this fails — `_encode` returns
    `None` for the shape `events.py` actually relays for `task-logs:stream`
    (chunk nested under `payload`, see `events.py:355`), dropping the whole
    chunk instead of truncating it.
    """
    data = {
        "event_type": "task-logs:stream",
        "payload": {"specId": "s1", "chunk": {"type": "text", "content": "x" * 20_000}},
    }

    encoded = relay._encode("ws:broadcast", data)

    assert encoded is not None
    assert len(encoded.encode()) <= relay._MAX_PAYLOAD
    decoded = json.loads(encoded)
    assert decoded["truncated"] is True
    assert len(decoded["data"]["payload"]["chunk"]["content"]) < 20_000


# ── 14. review fix 7: is_connected() must reflect an ESTABLISHED connection ─


async def test_is_connected_reflects_live_connection_not_a_scheduled_task(
    monkeypatch,
) -> None:
    """MUTATION: change `is_connected()` to `return _connection is not None`
    (drop the `is_closed()` half) ⇒ this fails — it would read `True` for a
    connection that has already died, which is exactly the "scheduled but
    not actually relaying" state this predicate exists to distinguish (#804
    fix 7: `main.py`'s boot log can't tell a working relay from a pod stuck
    retrying `connect()` using `_listener_task is not None` alone).
    """
    assert relay.is_connected() is False  # no connection at all

    conn = _FakeConnection([])
    monkeypatch.setattr(relay, "_connection", conn)
    assert relay.is_connected() is True

    conn.closed = True  # the connection died but nothing has cleared it yet
    assert relay.is_connected() is False


# ── 15. review fix 6: stop_listener must not hang on a stuck teardown ───────


class _HangingCloseConnection:
    """A connection whose ``close()`` takes far longer than any sane
    shutdown timeout — a partitioned socket. Finite (not truly infinite) so a
    mutation that removes the bound fails FAST with a clear elapsed-time
    assertion instead of hanging the test runner itself.
    """

    def __init__(self, hang_seconds: float = 10.0) -> None:
        self.closed = False
        self._hang_seconds = hang_seconds

    async def close(self) -> None:
        await asyncio.sleep(self._hang_seconds)

    def is_closed(self) -> bool:
        return self.closed


async def test_stop_listener_does_not_hang_on_a_stuck_close(monkeypatch) -> None:
    """MUTATION: drop the `asyncio.wait_for(..., timeout=_STOP_TIMEOUT)` around
    `_connection.close()` in `stop_listener()` (call `await _connection.close()`
    bare) ⇒ this fails — `stop_listener()` would take the full ~10s the fake
    connection's `close()` hangs for (a real partitioned socket has no such
    ceiling at all) instead of returning in about `_STOP_TIMEOUT`.
    """
    monkeypatch.setattr(relay, "_connection", _HangingCloseConnection())
    monkeypatch.setattr(relay, "_STOP_TIMEOUT", 0.2)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await relay.stop_listener()
    elapsed = loop.time() - start

    assert elapsed < 2.0, f"stop_listener() took {elapsed}s — the timeout bound was not applied"
    assert relay._connection is None


# ── 16. review fix 9a: an unrecognised TaskPhase must dedupe, not crash ─────


async def test_dispatch_task_progress_dedupes_unknown_phase(monkeypatch, caplog) -> None:
    """MUTATION: remove the `_warned_unknown_phases` dedupe in
    `_dispatch_task_progress` (warn on every call, matching the pre-fix
    unknown-``kind`` behavior) ⇒ this fails — three notifications would log
    three times instead of once, the same noise the unknown-kind dedupe
    exists to prevent, in the same rolling-deploy scenario.
    """
    _dispatch._warned_unknown_phases.clear()
    captured: list[Any] = []

    async def _fake_deliver_local_progress(progress: Any) -> None:
        captured.append(progress)

    monkeypatch.setattr(
        get_agent_service(), "_deliver_local_progress", _fake_deliver_local_progress
    )

    data = {
        "task_id": "t1",
        "phase": "some_future_phase_this_pod_does_not_know",
        "message": "hi",
        "timestamp": "2026-01-01T00:00:00",
        "subtask": None,
        "subtask_index": None,
        "subtask_total": None,
        "percentage": 50.0,
        "overall_progress": None,
        "sequence_number": 1,
        "started_at": None,
        "data": {},
    }

    with caplog.at_level(logging.WARNING, logger=_dispatch.__name__):
        for _ in range(3):
            await _dispatch.dispatch("task:progress", data)  # must never raise

    matches = [r for r in caplog.records if "unknown phase" in r.getMessage()]
    assert len(matches) == 1
    assert captured == []  # never delivered — an invalid TaskProgress would be worse


# ── 17. review fix 9b: a non-dict decoded payload must not kill the listener ─


async def test_listener_skips_non_dict_payload_without_dying(monkeypatch) -> None:
    """MUTATION: remove the `isinstance(message, dict)` check in
    `_listen_on()` ⇒ this fails — `message.get(...)` on a plain int (a
    well-formed JSON value, `json.loads("5") == 5`) raises `AttributeError`,
    which escapes to the reconnect branch and costs this pod its listener
    connection and a full reconnect over ONE malformed notification.
    """
    bad_payload = "5"  # decodes to the int 5, not a dict
    good_payload = json.dumps(
        {"origin": "other-pod:deadbeef", "kind": "task:log", "data": {"marker": "good"}}
    )
    fake_conn = _FakeConnection([("pfactory_ws", bad_payload), ("pfactory_ws", good_payload)])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=fake_conn))

    delivered: list[tuple[str, dict]] = []

    async def deliver(kind: str, data: dict) -> None:
        delivered.append((kind, data))

    await relay.start_listener(deliver)
    await _wait_until(lambda: len(delivered) >= 1)

    # The listener survived the bad payload and kept processing the queue —
    # if it had died and reconnected instead, `deliver` would still
    # eventually see the good payload, just via a second connection.
    assert delivered == [("task:log", {"marker": "good"})]


# ── review finding 5: one wedged local client must not stall everyone else ──


class _WedgedWebSocket:
    """A client whose ``send_text`` never returns — a browser that stopped
    reading, indistinguishable at the socket level from one that's merely
    slow."""

    def __init__(self) -> None:
        self.closed = False

    async def send_text(self, _message: str) -> None:
        await asyncio.sleep(3600)

    async def close(self) -> None:
        self.closed = True


class _DeadWebSocket:
    """A client whose ``send_text`` raises immediately — a genuinely closed
    socket, as opposed to a merely slow one (#804 finding 3: these two must
    NOT be treated the same way)."""

    def __init__(self) -> None:
        self.closed = False

    async def send_text(self, _message: str) -> None:
        raise ConnectionResetError("boom: connection reset")

    async def close(self) -> None:
        self.closed = True


async def test_wedged_client_does_not_block_delivery_to_a_healthy_one(monkeypatch) -> None:
    """MUTATION 1: remove the `asyncio.wait_for(ws.send_text(message),
    timeout=_SEND_TIMEOUT)` wrap in `events._send_or_skip` (call
    `ws.send_text(message)` bare) ⇒ this fails — the wedged client's
    `send_text` never returns, so the healthy client never gets its turn
    (bounded by this test's own timeout, not a real hang).

    MUTATION 2 (#804 round 5 finding 3, reversing round 4's fix here): merge
    `_send_or_skip`'s `except TimeoutError: return` into the generic
    `except Exception: disconnected.append(ws)` path ⇒ this fails on the
    `wedged in active_connections` assertion — a timeout must SKIP that
    client for this message only, not evict it: evicting on a mere timeout
    converts "slow" into "disconnected", which is worse than doing nothing,
    since the client never gets a chance to catch up on the next message.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.2)
    wedged = _WedgedWebSocket()
    healthy = _FakeWebSocket()
    events._register_client(wedged, None)
    events._register_client(healthy, None)
    try:
        await asyncio.wait_for(events._deliver_local_broadcast("task:test", {"x": 1}), timeout=5.0)

        assert healthy.sent == [json.dumps({"type": "task:test", "payload": {"x": 1}})]
        assert wedged in events.active_connections, "a timeout must skip, not evict"
        assert healthy in events.active_connections
    finally:
        events._unregister_client(wedged)
        events._unregister_client(healthy)


async def test_a_genuinely_dead_client_is_evicted_and_its_socket_closed(monkeypatch) -> None:
    """#804 finding 3 (final call): unlike a timeout, a genuine send failure
    (a closed socket, not merely a slow one) must still be evicted THROUGH
    `_evict_client` — unregistered AND closed, not just unregistered. This
    distinction is the entire point of splitting `TimeoutError` out from the
    generic `except Exception` in `_send_or_skip`.

    MUTATION 1: change `_send_or_skip`'s generic `except Exception:
    disconnected.append(ws)` to `except Exception: return` (skip, like a
    timeout) ⇒ this fails — the dead client would stay registered forever.

    MUTATION 2: change `_deliver_local_broadcast`'s `await
    _evict_client(ws)` back to a bare `_unregister_client(ws)` (no close)
    ⇒ this fails on `dead.closed` — the socket would be unregistered but
    never closed: a silent blackhole where the browser still looks connected
    but can never receive anything again.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.2)
    dead = _DeadWebSocket()
    healthy = _FakeWebSocket()
    events._register_client(dead, None)
    events._register_client(healthy, None)
    try:
        await asyncio.wait_for(events._deliver_local_broadcast("task:test", {"x": 1}), timeout=5.0)

        assert healthy.sent == [json.dumps({"type": "task:test", "payload": {"x": 1}})]
        assert dead not in events.active_connections
        assert dead.closed, "socket was unregistered but never closed — a phantom connection"
        assert healthy in events.active_connections
    finally:
        events._unregister_client(dead)
        events._unregister_client(healthy)


async def test_slow_clients_are_sent_to_concurrently_not_serially(monkeypatch) -> None:
    """#804 round 5 finding 3 (secondary): the delivery loop runs on the
    stdout path (`_emit_progress` → `broadcast_event` →
    `_deliver_local_broadcast`) — finding 4 already took Postgres off that
    path; a SEQUENTIAL per-client loop would still cost N x `_SEND_TIMEOUT`
    inline on it when N clients are slow at once.

    MUTATION: change `_deliver_local_broadcast`'s `asyncio.gather(...)` back
    to a sequential `for ws in ...: await _send_or_skip(...)` loop ⇒ this
    fails — 5 wedged clients would cost ~5x`_SEND_TIMEOUT` instead of ~1x.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.2)
    wedged_clients = [_WedgedWebSocket() for _ in range(5)]
    for ws in wedged_clients:
        events._register_client(ws, None)
    try:
        loop = asyncio.get_event_loop()
        start = loop.time()
        await asyncio.wait_for(events._deliver_local_broadcast("task:test", {"x": 1}), timeout=5.0)
        elapsed = loop.time() - start
        assert elapsed < 1.0, f"delivery took {elapsed}s — looks serialized, not concurrent"
    finally:
        for ws in wedged_clients:
            events._unregister_client(ws)


async def test_slow_client_skip_warning_is_deduped(monkeypatch, caplog) -> None:
    """#804 finding 3 (test previously measured nothing): registers a
    HEALTHY peer alongside the wedged client so the per-client reset path is
    actually exercised — with only one client registered, the reset branch
    (`_slow_client_state.pop(ws, None)` on a SUCCESSFUL send) is unreachable,
    and a module-level (rather than per-client) dedupe flag would pass this
    test even though it doesn't dedupe under real concurrent load (see the
    MUTATION below). Uses 2 calls, under `_MAX_CONSECUTIVE_TIMEOUTS` (3), so
    this test is about DEDUPE, not the separate eviction behavior.

    MUTATION: revert `_send_or_skip`'s per-client `_SlowClientState.warned`
    to a single module-level `_slow_client_warned` flag, reset on ANY
    client's successful send (round 5's design) ⇒ this fails — under
    `gather`, the healthy peer's near-instant success resets the shared flag
    before the wedged client's own timeout (5s later) gets a chance to check
    it, so EVERY call re-warns: 2 calls, 2 warnings, not 1.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    wedged = _WedgedWebSocket()
    healthy = _FakeWebSocket()
    events._register_client(wedged, None)
    events._register_client(healthy, None)
    try:
        with caplog.at_level(logging.WARNING, logger=events.__name__):
            for _ in range(2):
                await asyncio.wait_for(
                    events._deliver_local_broadcast("task:test", {"x": 1}), timeout=5.0
                )

        matches = [r for r in caplog.records if "send timed out" in r.getMessage()]
        assert len(matches) == 1
        assert wedged in events.active_connections, "under the threshold — must still be registered"
    finally:
        events._unregister_client(wedged)
        events._unregister_client(healthy)


async def test_a_client_stuck_timing_out_is_evicted_after_max_consecutive_timeouts(
    monkeypatch,
) -> None:
    """#804 finding 4: a client that NEVER drains — `_MAX_CONSECUTIVE_TIMEOUTS`
    timeouts in a row with no successful send in between — is reclassified
    as dead and evicted (closed + unregistered), not skipped forever.

    MUTATION: remove the `if state.consecutive_timeouts >=
    _MAX_CONSECUTIVE_TIMEOUTS: disconnected.append(ws); return` branch (skip
    unconditionally on every timeout, as finding 3 alone would) ⇒ this fails
    — the client would still be registered, and its socket still open, after
    `_MAX_CONSECUTIVE_TIMEOUTS` consecutive timeouts.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    wedged = _WedgedWebSocket()
    events._register_client(wedged, None)
    try:
        for _ in range(events._MAX_CONSECUTIVE_TIMEOUTS):
            await asyncio.wait_for(
                events._deliver_local_broadcast("task:test", {"x": 1}), timeout=5.0
            )

        assert wedged not in events.active_connections
        assert wedged.closed, "socket was unregistered but never closed — a phantom connection"
    finally:
        events._unregister_client(wedged)


async def test_inbound_queue_does_not_grow_past_the_cap_under_a_flood(monkeypatch) -> None:
    """MUTATION 1: create `_listen_on`'s queue with a bare `asyncio.Queue()`
    (no `maxsize`) instead of `maxsize=_QUEUE_MAXSIZE` ⇒ this fails — it
    would grow without bound under a flood instead of staying capped at 5.

    MUTATION 2 (#804 round 5 finding H2 — the original version of this test
    only asserted the cap, which passes identically for drop-NEWEST, leaving
    the entire justification for drop-OLDEST unpinned): change
    `_put_inbound_dropping_oldest` to drop-newest instead — reject a new
    arrival outright once the queue is full, rather than evicting the head
    to admit it ⇒ this fails on the second assertion — the flood's LAST,
    freshest item (marker 49) never lands in the queue at all under
    drop-newest (every arrival past the first 5 is rejected), so `deliver`
    never sees it; drop-oldest instead evicts stale entries to admit each new
    one, so the most recent notification — the one a catching-up pod most
    needs — always survives.
    """
    monkeypatch.setattr(relay, "_QUEUE_MAXSIZE", 5)

    qsizes: list[int] = []
    original_put = relay._put_inbound_dropping_oldest

    def _spy_put(queue: asyncio.Queue, item: Any) -> None:
        original_put(queue, item)
        qsizes.append(queue.qsize())

    monkeypatch.setattr(relay, "_put_inbound_dropping_oldest", _spy_put)

    # A flood: far more notifications than the cap, all delivered
    # synchronously inside `add_listener` — mimicking a burst of NOTIFYs
    # arriving before the listener loop gets a single chance to drain any.
    # Each carries a distinct marker so which ones survive is observable.
    flood = [
        (
            "pfactory_ws",
            json.dumps({"origin": "other-pod:x", "kind": "task:log", "data": {"marker": i}}),
        )
        for i in range(50)
    ]
    fake_conn = _FakeConnection(flood)
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=fake_conn))

    delivered: list[Any] = []

    async def deliver(_kind: str, data: dict) -> None:
        delivered.append(data.get("marker"))

    await relay.start_listener(deliver)
    await _wait_until(lambda: len(qsizes) >= 50)
    await _wait_until(lambda: len(delivered) >= 5, deadline_seconds=3.0)

    assert max(qsizes) <= 5
    assert 49 in delivered, "the freshest notification was dropped instead of the oldest"


# ── review round 3: the listener and the drain must never share a connection ─


class _BusyThenExecuteConnection(_MinimalConnection):
    """Tracks whether another operation is "in flight" when ``execute`` is
    called — models the asyncpg ``InterfaceError: another operation is in
    progress`` symptom an independent review reproduced against real
    Postgres when the listener's `LISTEN` and the drain's `execute` shared
    one connection."""

    def __init__(self, busy_seconds: float) -> None:
        super().__init__()
        self._busy_seconds = busy_seconds
        self.busy = False
        self.violation = False

    async def add_listener(self, _channel: str, _callback: Any) -> None:
        self.busy = True
        await asyncio.sleep(self._busy_seconds)
        self.busy = False

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        if self.busy:
            self.violation = True


async def test_drain_and_listener_use_separate_connections(monkeypatch) -> None:
    """MUTATION: give the drain the LISTENER's own connection instead of a
    pool of its own (e.g. change `start_listener` to pass `_connection` into
    `_drain_outbox` instead of the drain's own `asyncpg.create_pool`ed one)
    ⇒ this fails — a `publish` landing while the shared connection's
    `add_listener` is still in flight raises the asyncpg
    `InterfaceError: another operation is in progress` symptom, modelled
    here as `violation`.
    """
    listener_conn = _BusyThenExecuteConnection(busy_seconds=1.0)
    drain_conn = _FakeConnection([])
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(listener_conn))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool(drain_conn)))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is listener_conn)

    # Publish while the listener's `add_listener` is (very likely) still
    # "in flight" (1s) — must land on the DRAIN's own connection, untouched
    # by that.
    await relay.publish("task:log", {"task_id": "t1", "content": "hi"})
    await _wait_until(lambda: len(drain_conn.calls) >= 1, deadline_seconds=3.0)

    assert not listener_conn.violation
    assert len(drain_conn.calls) == 1


async def test_stop_listener_cancels_drain_before_listener_teardown(monkeypatch) -> None:
    """MUTATION: swap `stop_listener`'s order (cancel/await the listener task
    before the drain task) ⇒ this fails — `order` would record
    `["listener", "drain"]` instead of `["drain", "listener"]`.
    """

    async def _tracked(name: str, order: list[str], started: asyncio.Event) -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            order.append(name)
            raise

    order: list[str] = []
    listener_started = asyncio.Event()
    drain_started = asyncio.Event()
    listener_task = asyncio.create_task(_tracked("listener", order, listener_started))
    drain_task = asyncio.create_task(_tracked("drain", order, drain_started))
    # Let both tasks actually start (reach their `await asyncio.sleep`)
    # before cancelling either — otherwise a not-yet-scheduled task's
    # cancellation can be observed oddly depending on event-loop timing,
    # which is not what this test is trying to pin down.
    await listener_started.wait()
    await drain_started.wait()
    monkeypatch.setattr(relay, "_listener_task", listener_task)
    monkeypatch.setattr(relay, "_drain_task", drain_task)
    monkeypatch.setattr(relay, "_outbox", asyncio.Queue())
    monkeypatch.setattr(relay, "_connection", None)
    monkeypatch.setattr(relay, "_publish_pool", None)

    await relay.stop_listener()

    assert order == ["drain", "listener"]


async def test_start_listener_ignores_a_second_call_instead_of_leaking(monkeypatch) -> None:
    """#804 round 5 finding 6: `start_listener` has no guard against a
    second call — unreachable from `lifespan` today, but reachable from
    tests and any future restart path.

    MUTATION: remove the `if _listener_task is not None: return` guard ⇒
    this fails — the second call would overwrite `_listener_task`/
    `_drain_task`/`_outbox`/`_publish_pool` with a second set, leaking the
    FIRST listener and drain tasks (still running, the drain still awaiting
    a queue nobody publishes to anymore) — asserted here as the FIRST
    connection's `asyncpg.connect` being called only once.
    """
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    connect_mock = AsyncMock(return_value=_FakeConnection([]))
    monkeypatch.setattr(relay.asyncpg, "connect", connect_mock)
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool()))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    first_listener_task = relay._listener_task
    first_drain_task = relay._drain_task
    await _wait_until(lambda: connect_mock.await_count >= 1)

    await relay.start_listener(deliver)  # must be a no-op

    assert relay._listener_task is first_listener_task
    assert relay._drain_task is first_drain_task
    assert connect_mock.await_count == 1


async def test_drain_failure_warning_is_deduped(monkeypatch, caplog) -> None:
    """MUTATION: remove the `_drain_failure_warned` dedupe in `_drain_outbox`
    (warn on every failed attempt) ⇒ this fails — two failed retries of
    the SAME item (under `_DRAIN_MAX_ATTEMPTS`) before it finally succeeds
    would log two warnings instead of one — a traceback burst at exactly the
    moment the pod is already unhealthy.
    """
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (0,))  # keep the test fast
    relay._drain_failure_warned = False
    dying = _DropsOnConnectionConnection()
    healthy = _FakeConnection([])
    # Two failures (under the 3-attempt cap, so still retried) then success —
    # never hits the give-up path this test isn't about.
    pool = _FakePool(dying, dying, healthy)
    outbox: asyncio.Queue[str] = asyncio.Queue()
    outbox.put_nowait(relay._encode("task:log", {"task_id": "t1", "content": "x"}))

    # `_drain_outbox` never returns on its own (it's the drain task's whole
    # body) — run it as a background task and cancel once the item has
    # finally landed, rather than asserting on a return value it doesn't
    # have.
    task = asyncio.create_task(relay._drain_outbox(pool, outbox))
    try:
        with caplog.at_level(logging.WARNING, logger=relay.__name__):
            await _wait_until(lambda: len(healthy.calls) >= 1, deadline_seconds=3.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    matches = [r for r in caplog.records if "outbox drain failed" in r.getMessage()]
    assert len(matches) == 1


class _HangingRemoveListenerConnection(_MinimalConnection):
    def is_closed(self) -> bool:
        return True

    async def remove_listener(self, _channel: str, _callback: Any) -> None:
        await asyncio.sleep(10.0)


async def test_listen_on_teardown_is_bounded_when_remove_listener_hangs(monkeypatch) -> None:
    """MUTATION: drop the `asyncio.wait_for(..., timeout=_STOP_TIMEOUT)` around
    `conn.remove_listener(...)` in `_listen_on`'s `finally` (call it bare) ⇒
    this fails — `_listen_on` would take the full ~10s the fake's
    `remove_listener` hangs for, instead of returning in about `_STOP_TIMEOUT`.
    """
    monkeypatch.setattr(relay, "_STOP_TIMEOUT", 0.2)
    monkeypatch.setattr(relay, "_POLL_INTERVAL", 0.05)
    conn = _HangingRemoveListenerConnection()

    async def deliver(kind: str, data: dict) -> None:
        pass

    loop = asyncio.get_event_loop()
    start = loop.time()
    await relay._listen_on(conn, deliver)
    elapsed = loop.time() - start

    assert elapsed < 2.0, f"_listen_on() took {elapsed}s — the teardown timeout was not applied"


# ── #804 round 6 remainder: finding A — the plan's central invariant ───────
#
# "Deliver locally first, unconditionally, so a relay failure can never
# silence a browser on the emitting pod" is the entire reason publish-then-
# deliver was rejected when this design was chosen — and nothing pinned the
# ORDER before these three tests. Local delivery still happening (which
# every OTHER test already checks) is not the same claim as local delivery
# happening FIRST.


async def test_broadcast_event_delivers_locally_before_publishing(monkeypatch) -> None:
    """MUTATION: swap the two lines in `broadcast_event` (publish before
    `_deliver_local_broadcast`) ⇒ this fails — `order` would record
    `["publish", "deliver"]` instead of `["deliver", "publish"]`.
    """
    order: list[str] = []

    async def fake_deliver(_event_type: str, _payload: dict) -> None:
        order.append("deliver")

    async def fake_publish(_kind: str, _data: dict) -> None:
        order.append("publish")

    monkeypatch.setattr(events, "_deliver_local_broadcast", fake_deliver)
    monkeypatch.setattr(relay, "publish", fake_publish)

    await events.broadcast_event("task:test", {"x": 1})

    assert order == ["deliver", "publish"]


async def test_send_to_user_delivers_locally_before_publishing(monkeypatch) -> None:
    """MUTATION: swap the two lines in `send_to_user` ⇒ this fails, same
    reasoning as the broadcast case above."""
    order: list[str] = []

    async def fake_deliver(_user_id: str, _event_type: str, _payload: dict) -> None:
        order.append("deliver")

    async def fake_publish(_kind: str, _data: dict) -> None:
        order.append("publish")

    monkeypatch.setattr(events, "_deliver_local_to_user", fake_deliver)
    monkeypatch.setattr(relay, "publish", fake_publish)

    await events.send_to_user("user-a", "task:test", {"x": 1})

    assert order == ["deliver", "publish"]


async def test_send_to_org_delivers_locally_before_publishing(monkeypatch) -> None:
    """MUTATION: swap the two lines in `send_to_org` ⇒ this fails, same
    reasoning as the broadcast case above."""
    order: list[str] = []

    async def fake_deliver(_org_id: str, _event_type: str, _payload: dict) -> None:
        order.append("deliver")

    async def fake_publish(_kind: str, _data: dict) -> None:
        order.append("publish")

    monkeypatch.setattr(events, "_deliver_local_to_org", fake_deliver)
    monkeypatch.setattr(relay, "publish", fake_publish)

    await events.send_to_org("org-a", "task:test", {"x": 1})

    assert order == ["deliver", "publish"]


# ── finding B: the keepalive is the other half of eviction ─────────────────


class _KeepaliveWedgedWebSocket:
    """Models a client that reaches `events_websocket`'s OWN keepalive path
    (not the `_deliver_local_*` delivery path): its `receive_text` always
    times out (so the endpoint falls into the ping branch), and its
    `send_text` (the ping itself) never returns."""

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        await asyncio.sleep(3600)
        return ""  # pragma: no cover — never reached

    async def send_text(self, _message: str) -> None:
        await asyncio.sleep(3600)


async def test_keepalive_ping_is_bounded_on_a_wedged_socket(monkeypatch) -> None:
    """MUTATION: remove the `asyncio.wait_for(websocket.send_text(...),
    timeout=_SEND_TIMEOUT)` wrap around the keepalive ping (call
    `websocket.send_text(...)` bare) ⇒ this fails — `events_websocket` would
    never return (bounded here by this test's own outer timeout, not a real
    hang), staying parked on the wedged ping forever instead of reaching
    `finally: _unregister_client`.
    """
    monkeypatch.setattr(events, "_RECEIVE_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    monkeypatch.setattr(events, "authenticate_websocket", AsyncMock(return_value=None))

    ws = _KeepaliveWedgedWebSocket()
    try:
        await asyncio.wait_for(events.events_websocket(ws), timeout=5.0)
    finally:
        events._unregister_client(ws)


class _PingingWedgedWebSocket:
    """Models the ACTUAL hot path (#804 round 7 finding 1): a client that
    keeps pinging (`receive_text` returns "ping" immediately, matching the
    frontend's 25s heartbeat) but stopped reading — a backgrounded tab, or a
    full receive window during a log burst — so its "pong" reply's
    `send_text` never returns. `_KeepaliveWedgedWebSocket` above never
    exercises this branch at all: its `receive_text` hangs, so "ping" is
    never received and this file never contained the string "pong" in a
    test until now."""

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        return "ping"

    async def send_text(self, _message: str) -> None:
        await asyncio.sleep(3600)


async def test_pong_reply_is_bounded_on_a_wedged_socket(monkeypatch) -> None:
    """#804 round 7 finding 1: round 6's item B bounded the keepalive ping
    two lines below this one and left the "pong" reply bare — the REVERSE of
    what matters, since the frontend pings every 25s, so nearly every client
    takes THIS branch, not the keepalive fallback.

    MUTATION: remove the `asyncio.wait_for(websocket.send_text("pong"),
    timeout=_SEND_TIMEOUT)` wrap (call `websocket.send_text("pong")` bare)
    ⇒ this fails — `events_websocket` would never return (bounded here by
    this test's own outer timeout, not a real hang).
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    monkeypatch.setattr(events, "authenticate_websocket", AsyncMock(return_value=None))

    ws = _PingingWedgedWebSocket()
    try:
        await asyncio.wait_for(events.events_websocket(ws), timeout=5.0)
    finally:
        events._unregister_client(ws)


async def test_evict_clients_close_is_bounded(monkeypatch) -> None:
    """#804 round 6 finding B: `_evict_client`'s `await ws.close()` usually
    runs on a socket that just raised from `send_text`, but a half-open peer
    can hang `close()` too. Pins the PER-CLIENT bound in isolation; see
    `test_evicting_several_half_open_clients_costs_about_one_timeout_not_n`
    (#804 round 9 finding 2) for the separate BATCH-cost concern — bounding
    one client's close doesn't help if the batch loop calling it is
    sequential.

    MUTATION: remove the `asyncio.wait_for(ws.close(), timeout=_SEND_TIMEOUT)`
    wrap in `_evict_client` (call `ws.close()` bare) ⇒ this fails — bounded
    here by this test's own outer timeout, not a real hang.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)

    class _HangingCloseWebSocket:
        async def close(self) -> None:
            await asyncio.sleep(3600)

    ws = _HangingCloseWebSocket()
    await asyncio.wait_for(events._evict_client(ws), timeout=5.0)


class _DeadWithHangingCloseWebSocket:
    """A client whose send fails IMMEDIATELY (a genuine failure, evicted on
    the first try, no `_SEND_TIMEOUT` delay from the send itself) but whose
    `close()` hangs like a half-open peer — isolates the EVICTION BATCH's
    own cost from the separate cost of the send `gather` earlier in the same
    call."""

    async def send_text(self, _message: str) -> None:
        raise ConnectionResetError("boom: connection reset")

    async def close(self) -> None:
        await asyncio.sleep(3600)


async def test_evicting_several_half_open_clients_costs_about_one_timeout_not_n(
    monkeypatch,
) -> None:
    """#804 round 9 finding 2: bounding each `_evict_client`'s own `close()`
    (finding B) caps the PER-CLIENT cost, but the batch loop calling it
    SEQUENTIALLY still paid N x that bound — measured at 5 half-open
    clients, ~5x`_SEND_TIMEOUT` of serial closes on top of the ~1x
    `_SEND_TIMEOUT` the send `gather` already costs. That sits inline on the
    `_emit_progress` -> stdout path and, because `_listen_on` awaits
    `deliver(...)` inline in its own receive loop, stalls INBOUND relayed
    events from every OTHER pod for the same stretch too — the exact
    pod-wide stall this module exists to prevent, relocated from send to
    close. Worse, timeout-eviction reaches exactly the clients whose
    `close()` is most likely to hang the full timeout — half-open ones — so
    a NAT or load-balancer drop strands a whole cohort at once.

    MUTATION: revert `_deliver_local_broadcast`'s `asyncio.gather(*(
    _evict_client(ws) for ws in disconnected), return_exceptions=True)` back
    to a sequential `for ws in disconnected: await _evict_client(ws)` loop
    ⇒ this fails — 5 half-open clients would cost ~5x`_SEND_TIMEOUT`
    (~1.0s), not ~1x (~0.2s).
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.2)
    dead_clients = [_DeadWithHangingCloseWebSocket() for _ in range(5)]
    for ws in dead_clients:
        events._register_client(ws, None)
    try:
        loop = asyncio.get_event_loop()
        start = loop.time()
        await asyncio.wait_for(events._deliver_local_broadcast("task:test", {"x": 1}), timeout=5.0)
        elapsed = loop.time() - start

        assert elapsed < 0.6, f"evicting 5 half-open clients took {elapsed}s — looks serialized"
    finally:
        for ws in dead_clients:
            events._unregister_client(ws)


# ── finding C: publish-pool teardown is untested and leaks ─────────────────


async def test_stop_listener_closes_the_publish_pool(monkeypatch) -> None:
    """#804 round 6 finding C: every existing shutdown test sets
    `_publish_pool = None` before calling `stop_listener`, so nothing
    exercises the close path at all — deleting `stop_listener`'s whole
    `_publish_pool` close/terminate block left every test green while
    leaking up to 2 backend connections per stop.

    MUTATION: delete the `if _publish_pool is not None: ...` block from
    `stop_listener` ⇒ this fails — `pool.closed` stays `False` and
    `relay._publish_pool` stays set.
    """
    pool = _FakePool()
    monkeypatch.setattr(relay, "_publish_pool", pool)
    monkeypatch.setattr(relay, "_listener_task", None)
    monkeypatch.setattr(relay, "_drain_task", None)
    monkeypatch.setattr(relay, "_outbox", None)
    monkeypatch.setattr(relay, "_connection", None)

    await relay.stop_listener()

    assert pool.closed
    assert relay._publish_pool is None


async def test_stop_listener_terminates_the_pool_if_close_hangs(monkeypatch) -> None:
    """MUTATION: remove the `except Exception: _publish_pool.terminate()`
    fallback in `stop_listener` (let a failing/timed-out `close()` just be
    swallowed without a hard fallback) ⇒ this fails — `pool.terminated`
    stays `False` and the pool is never actually torn down.
    """
    monkeypatch.setattr(relay, "_STOP_TIMEOUT", 0.05)

    class _HangingClosePool(_FakePool):
        async def close(self) -> None:
            await asyncio.sleep(3600)

    pool = _HangingClosePool()
    monkeypatch.setattr(relay, "_publish_pool", pool)
    monkeypatch.setattr(relay, "_listener_task", None)
    monkeypatch.setattr(relay, "_drain_task", None)
    monkeypatch.setattr(relay, "_outbox", None)
    monkeypatch.setattr(relay, "_connection", None)

    await asyncio.wait_for(relay.stop_listener(), timeout=5.0)

    assert pool.terminated


# ── finding D: dispatch()'s own failure warning was not deduped ────────────


async def test_dispatch_failure_warning_is_deduped_per_kind(monkeypatch, caplog) -> None:
    """#804 round 6 finding D: the third instance of this class after the
    drain warning and the unknown-phase warning. Concrete scenario: a
    rolling deploy adds a field to `TaskProgress`, so `TaskProgress(**data)`
    raises on the older pod for EVERY tick until the deploy finishes.

    MUTATION: remove the `_warned_dispatch_failure` dedupe in `dispatch()`
    (warn on every failure) ⇒ this fails — three failing dispatches of the
    SAME kind would log three warnings instead of one.
    """
    _dispatch._warned_dispatch_failure.clear()

    async def _boom(_data: dict) -> None:
        raise RuntimeError("boom: bad payload")

    monkeypatch.setitem(_dispatch._HANDLERS, "task:log", _boom)

    with caplog.at_level(logging.WARNING, logger=_dispatch.__name__):
        for _ in range(3):
            await _dispatch.dispatch("task:log", {"task_id": "t1"})  # must not raise

    matches = [r for r in caplog.records if "local delivery failed" in r.getMessage()]
    assert len(matches) == 1


# ── finding E: the re-entry guard was checked before an await ──────────────


async def test_start_listener_concurrent_calls_only_start_once(monkeypatch) -> None:
    """#804 round 6 finding E: `start_listener`'s guard checked
    `_listener_task is not None` and then awaited `create_pool` — two
    concurrent calls could both pass the check before either reached that
    await (asyncio only switches tasks AT an await point), each creating its
    own pool/listener, with `stop_listener` only ever closing one of them.

    MUTATION: move `_listener_task = asyncio.create_task(...)` back to AFTER
    `await asyncpg.create_pool(...)` ⇒ this fails — `create_pool` gets
    awaited (and `pool_mock` called) twice, once per concurrent call,
    instead of once — both calls' synchronous prefixes (URL resolution, the
    guard check) run back-to-back before either yields, so both pass the
    guard while it's still unset.
    """
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=_FakeConnection([])))

    call_count = {"n": 0}

    async def _create_pool(*_args: Any, **_kwargs: Any) -> Any:
        call_count["n"] += 1
        # A REAL yield point, unlike a bare `AsyncMock` (which returns
        # without ever suspending, so it can't reproduce a scheduling race
        # at all): this is what actually lets the SECOND concurrent call run
        # its own synchronous prefix while the FIRST is suspended here,
        # exactly like a real `create_pool` awaiting the network would.
        await asyncio.sleep(0)
        return _FakePool()

    monkeypatch.setattr(relay.asyncpg, "create_pool", _create_pool)

    async def deliver(kind: str, data: dict) -> None:
        pass

    await asyncio.gather(relay.start_listener(deliver), relay.start_listener(deliver))

    assert call_count["n"] == 1


async def test_start_listener_fully_fails_and_can_be_retried_if_create_pool_raises(
    monkeypatch,
) -> None:
    """#804 round 9 finding 1 — a blocker created by round 6 finding E's own
    fix (mine, per the team lead, not a new defect this round introduces):
    claiming `_listener_task` before `await asyncpg.create_pool(...)` closed
    the concurrent-call race, but opened this one — if `create_pool` itself
    raises (Postgres not yet accepting connections during a rolling deploy,
    the exact case `main.py`'s own startup catch anticipates),
    `_listener_task` stayed claimed forever while `_drain_task`/`_outbox`
    were never assigned. That pod's LISTENER still works fine (it retries
    and connects on its own), but `publish()` no-ops forever at its
    `_drain_task is None` guard — the pod relays NOTHING outbound for its
    entire remaining lifetime — and the re-entry guard refuses every retry
    since `_listener_task` is still set. No test in the suite covered
    `create_pool` raising at all: every `create_pool` monkeypatch elsewhere
    returns a `_FakePool`.

    MUTATION: remove the `except Exception: ... _listener_task = None;
    raise` rollback (restore the pre-round-9 behaviour) ⇒ this fails —
    `_listener_task` stays non-`None` after the failed call, and the second
    `start_listener` call is silently ignored by the re-entry guard instead
    of actually starting.
    """
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=_FakeConnection([])))
    create_pool_mock = AsyncMock(
        side_effect=[RuntimeError("boom: Postgres not up yet"), _FakePool()]
    )
    monkeypatch.setattr(relay.asyncpg, "create_pool", create_pool_mock)

    async def deliver(kind: str, data: dict) -> None:
        pass

    with pytest.raises(RuntimeError, match="boom: Postgres not up yet"):
        await relay.start_listener(deliver)

    assert relay._listener_task is None, "a failed start left the re-entry guard permanently set"
    assert relay._drain_task is None
    assert relay._outbox is None

    # A retry (main.py's own catch calling start_listener again, e.g. on the
    # next request) must actually start, not be silently ignored.
    await relay.start_listener(deliver)

    assert relay._listener_task is not None
    assert relay._drain_task is not None


# ── finding F: the outbox drop policy was unpinned (which items survive) ───


async def test_publish_drops_and_warns_once_when_outbox_is_full_and_keeps_freshest(
    monkeypatch, caplog
) -> None:
    """Supersedes the count-only version of this test (#804 round 6 finding
    F): asserting only the warning count and that nothing raised passes
    identically for drop-NEWEST, leaving the drop-OLDEST policy unpinned —
    the exact inverse of the inbound-queue hole already fixed.

    MUTATION: revert `_put_outbox_dropping_oldest` to drop-newest (reject a
    new arrival outright once the queue is full, instead of evicting the
    head to admit it) ⇒ this fails — the outbox would retain `x0`-`x2`
    instead of the freshest `x7`-`x9`.
    """
    monkeypatch.setattr(relay, "_QUEUE_MAXSIZE", 3)
    drain_conn = _HangingExecuteConnection()
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", _connect_sequence(_FakeConnection([])))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool(drain_conn)))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)

    with caplog.at_level(logging.WARNING, logger=relay.__name__):
        for i in range(10):
            await relay.publish("task:log", {"task_id": "t1", "content": f"x{i}"})  # must not raise

    matches = [r for r in caplog.records if "outbox full" in r.getMessage()]
    assert len(matches) == 1

    remaining = [json.loads(item)["data"]["content"] for item in list(relay._outbox._queue)]
    assert remaining == ["x7", "x8", "x9"], (
        f"expected the 3 FRESHEST items to survive (drop-oldest), got {remaining}"
    )


# ── finding G: four more mutations that survived ────────────────────────────


async def test_encode_does_not_mutate_the_callers_data_dict() -> None:
    """MUTATION: remove `copy.deepcopy(payload)` in `_encode` (truncate the
    caller's own dict in place instead of a copy) ⇒ this fails — the
    caller's `data` dict would come back mutated (`content` shrunk), which
    matters because callers (e.g. `agent_service._emit_log`) hand the SAME
    object to local delivery immediately before this runs.
    """
    original_content = "x" * 20_000
    data = {"task_id": "t1", "content": original_content}
    data_snapshot = {"task_id": "t1", "content": original_content}

    encoded = relay._encode("task:log", data)

    assert encoded is not None
    assert data == data_snapshot, "caller's data dict was mutated by _encode"


class _SignalingConnection(_MinimalConnection):
    """A healthy connection that sets an `asyncio.Event` the instant its
    `execute` lands — lets a test await a `Future`-based signal instead of
    polling with `asyncio.sleep` (#804 round 6 finding G: several of these
    tests spy on `asyncio.sleep` itself, so polling with it would corrupt
    the very thing being measured)."""

    def __init__(self, event: asyncio.Event) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._event = event

    async def execute(self, _sql: str, _channel: str, payload: str, **_kwargs: Any) -> None:
        self.calls.append(payload)
        self._event.set()


async def test_drain_retry_backoff_escalates_with_each_attempt(monkeypatch) -> None:
    """MUTATION: remove `attempt += 1` from `_drain_outbox`'s retry loop ⇒
    this fails — every retry would compute the same (or a non-escalating)
    backoff index instead of walking `_DRAIN_RETRY_BACKOFF` forward,
    hot-looping the retry at a fixed delay instead of backing off further
    each time.
    """
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (1, 2, 5))
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _spy_sleep(seconds: float) -> None:
        delays.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(relay.asyncio, "sleep", _spy_sleep)

    landed = asyncio.Event()
    dying = _DropsOnConnectionConnection()
    healthy = _SignalingConnection(landed)
    pool = _FakePool(dying, dying, healthy)
    outbox: asyncio.Queue[str] = asyncio.Queue()
    outbox.put_nowait(relay._encode("task:log", {"task_id": "t1", "content": "x"}))

    task = asyncio.create_task(relay._drain_outbox(pool, outbox))
    try:
        await asyncio.wait_for(landed.wait(), timeout=3.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert delays == [1, 2]


async def test_listener_reconnect_backoff_escalates_with_each_attempt(monkeypatch) -> None:
    """MUTATION: remove `attempt += 1` from `_run_listener`'s reconnect loop
    ⇒ this fails — every reconnect attempt would sleep
    `_RECONNECT_BACKOFF[0]` forever instead of escalating.
    """
    monkeypatch.setattr(relay, "_RECONNECT_BACKOFF", (1, 2, 5))
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _spy_sleep(seconds: float) -> None:
        delays.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(relay.asyncio, "sleep", _spy_sleep)
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")

    connecting = asyncio.Event()
    attempts = {"n": 0}

    async def _flaky_connect(_url: str) -> Any:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise RuntimeError("boom: connect failed")
        connecting.set()
        return _FakeConnection([])

    monkeypatch.setattr(relay.asyncpg, "connect", _flaky_connect)

    async def deliver(kind: str, data: dict) -> None:
        pass

    task = asyncio.create_task(relay._run_listener("fake://url", deliver))
    try:
        await asyncio.wait_for(connecting.wait(), timeout=3.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert delays == [1, 2]


async def test_listen_on_exits_when_only_is_closed_is_true(monkeypatch) -> None:
    """#804 round 6 finding G: `_listen_on`'s second break condition is
    `if item is None or conn.is_closed(): break` — a defensive
    belt-and-suspenders check for the connection dying WITHOUT the
    termination sentinel (`None`) ever landing, even though a real
    notification (not `None`) is sitting right there in the queue.

    MUTATION: narrow that condition to `if item is None: break` (drop
    `or conn.is_closed()`) ⇒ this fails — with a REAL, non-`None` item
    already queued and the connection ALREADY marked closed before the loop
    even starts, `deliver` would be called with the stale item instead of
    the loop exiting without ever calling it.
    """
    # Bounds the OTHER break condition's own poll (the `conn.is_closed()`
    # check inside the `TimeoutError` branch) so that, under the mutation,
    # this test still resolves quickly via that path instead of needing the
    # full `_POLL_INTERVAL` — it must still resolve FASTER under real code,
    # via the break this test targets, before ever reaching a timeout.
    monkeypatch.setattr(relay, "_POLL_INTERVAL", 0.2)
    notifications = [
        ("pfactory_ws", json.dumps({"origin": "other-pod:x", "kind": "task:log", "data": {}}))
    ]
    conn = _FakeConnection(notifications)
    conn.closed = True  # already closed BEFORE `_listen_on` even starts

    delivered: list[Any] = []

    async def deliver(kind: str, data: dict) -> None:
        delivered.append((kind, data))

    await asyncio.wait_for(relay._listen_on(conn, deliver), timeout=3.0)

    assert delivered == [], "deliver() ran on an item queued after the connection was closed"


def test_origin_nonce_makes_two_instances_in_one_process_distinct() -> None:
    """MUTATION: drop the `uuid4` nonce from `_ORIGIN` (HOSTNAME alone) ⇒
    this fails — two module "instances" sharing one HOSTNAME (simulated here
    via two reloads in the same process, since a real second instance is a
    second process) would produce IDENTICAL origins, which is exactly the
    collision `_ORIGIN`'s own docstring says the nonce exists to prevent.
    """
    first = importlib.reload(relay)._ORIGIN
    second = importlib.reload(relay)._ORIGIN

    assert first != second


# ── finding H: `_deliver_local_to_user`/`_deliver_local_to_org` had no tests ─


async def test_deliver_local_to_user_reaches_only_the_target_user() -> None:
    """#804 round 6 finding H: `_deliver_local_to_user` has no test anywhere
    in the repo despite being rewritten across rounds 4-6.

    MUTATION: change the filter from `client.user_id == user_id` to always
    `True` (deliver to everyone) ⇒ this fails — `other.sent` would no longer
    be empty.
    """
    target = _FakeWebSocket()
    other = _FakeWebSocket()
    events._register_client(target, {"id": "user-a"})
    events._register_client(other, {"id": "user-b"})
    try:
        await events._deliver_local_to_user("user-a", "task:test", {"x": 1})

        assert target.sent == [json.dumps({"type": "task:test", "payload": {"x": 1}})]
        assert other.sent == []
    finally:
        events._unregister_client(target)
        events._unregister_client(other)


async def test_deliver_local_to_org_reaches_members_and_legacy_not_other_orgs() -> None:
    """#804 round 6 finding H: pins the org path's three-way split — an org
    member receives, a member of a DIFFERENT org does not, and a legacy
    (no `user_id`) client — the `client.user_id is None` fallback — still
    receives despite not being a member of anything.

    MUTATION: drop the `client.user_id is None or` half of the filter
    (members only) ⇒ this fails — `legacy.sent` would come back empty.
    """
    member = _FakeWebSocket()
    other_org_member = _FakeWebSocket()
    legacy = _FakeWebSocket()
    member_client = events._register_client(member, {"id": "user-a"})
    member_client.org_ids = {"org-a"}
    other_client = events._register_client(other_org_member, {"id": "user-b"})
    other_client.org_ids = {"org-b"}
    events._register_client(legacy, None)
    try:
        await events._deliver_local_to_org("org-a", "task:test", {"x": 1})

        expected = json.dumps({"type": "task:test", "payload": {"x": 1}})
        assert member.sent == [expected]
        assert other_org_member.sent == []
        assert legacy.sent == [expected]
    finally:
        for ws in (member, other_org_member, legacy):
            events._unregister_client(ws)


async def test_deliver_local_to_user_timeout_skips_not_evicts(monkeypatch) -> None:
    """The timeout-skip-not-evict policy (finding 3) must hold on the
    to-user path too, not just broadcast — nothing exercised that before."""
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    wedged = _WedgedWebSocket()
    events._register_client(wedged, {"id": "user-a"})
    try:
        await asyncio.wait_for(
            events._deliver_local_to_user("user-a", "task:test", {"x": 1}), timeout=5.0
        )

        assert wedged in events._clients, "a timeout must skip, not evict"
    finally:
        events._unregister_client(wedged)


async def test_deliver_local_to_org_timeout_skips_not_evicts(monkeypatch) -> None:
    """The timeout-skip-not-evict policy (finding 3) must hold on the
    to-org path too, not just broadcast — nothing exercised that before."""
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    wedged = _WedgedWebSocket()
    client = events._register_client(wedged, {"id": "user-a"})
    client.org_ids = {"org-a"}
    try:
        await asyncio.wait_for(
            events._deliver_local_to_org("org-a", "task:test", {"x": 1}), timeout=5.0
        )

        assert wedged in events._clients, "a timeout must skip, not evict"
    finally:
        events._unregister_client(wedged)


# ── #804 round 7: finding 2 — the drain's acquire/execute bounds, tested ────
#
# The reviewer flagged these as the only parts of the diff with zero
# coverage. Both fakes below faithfully model asyncpg's OWN `timeout=`
# contract (hang internally, but let the timeout VALUE passed in bound the
# hang via a real `asyncio.wait_for`) rather than merely recording that some
# keyword happened to be passed — the point is proving the value actually
# reaches the pool/connection and does the bounding, not just that a
# `timeout=` argument exists syntactically.


class _BoundedHangContext:
    """An async context manager that hangs far longer than any sane
    timeout, bounded ONLY by whatever `timeout` value it's given — modelling
    what a REAL `asyncpg.Pool.acquire(timeout=...)` does against a dead
    connection."""

    def __init__(self, timeout: float | None) -> None:
        self._timeout = timeout

    async def __aenter__(self) -> Any:
        await asyncio.wait_for(asyncio.sleep(3600), timeout=self._timeout)
        return None  # pragma: no cover — wait_for always raises first

    async def __aexit__(self, *_exc: Any) -> None:
        pass


class _RealisticHangingAcquirePool:
    """A pool whose `acquire()` hangs against a dead connection, bounded
    only by the `timeout=` value `_drain_outbox` passes it — unlike
    `_FakePool`, which never enforces the timeout value at all."""

    def acquire(self, *, timeout: float | None = None) -> _BoundedHangContext:
        return _BoundedHangContext(timeout)


async def test_drain_passes_acquire_timeout_so_a_hung_acquire_is_escaped(monkeypatch) -> None:
    """MUTATION: drop `timeout=_ACQUIRE_TIMEOUT` from `pool.acquire(...)` in
    `_drain_outbox` (call `pool.acquire()` bare) ⇒ this fails — the fake's
    `asyncio.wait_for(..., timeout=None)` never times out, so the item is
    never dropped and `_drain_dropped_count` never advances (bounded here by
    `_wait_until`'s own deadline, not a real hang).
    """
    monkeypatch.setattr(relay, "_ACQUIRE_TIMEOUT", 0.05)
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (0,))
    pool = _RealisticHangingAcquirePool()
    outbox: asyncio.Queue[str] = asyncio.Queue()
    outbox.put_nowait(relay._encode("task:log", {"task_id": "t1", "content": "x"}))

    start_count = relay._drain_dropped_count
    task = asyncio.create_task(relay._drain_outbox(pool, outbox))
    try:
        await _wait_until(lambda: relay._drain_dropped_count > start_count, deadline_seconds=3.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _RealisticHangingExecuteConnection(_MinimalConnection):
    """A connection whose `execute()` hangs against a half-open socket,
    bounded only by the `timeout=` value `_drain_outbox` passes it — models
    the case the module's own comment describes: `acquire()` returns
    instantly once the pool holds a warm connection, and it's the unbounded
    `execute()` that then waits."""

    async def execute(
        self,
        _sql: str,
        _channel: str,
        _payload: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 — matches asyncpg.Connection.execute
    ) -> None:
        await asyncio.wait_for(asyncio.sleep(3600), timeout=timeout)


async def test_drain_passes_execute_timeout_so_a_hung_execute_is_escaped(monkeypatch) -> None:
    """#804 round 7 finding 2: `_ACQUIRE_TIMEOUT` alone does NOT bound the
    drain against a half-open socket or a mid-failover partition — once the
    pool holds a warm connection, `acquire()` returns instantly, and it was
    the UNBOUNDED `execute()` that then waited for however long TCP
    retransmission takes (Linux default ~15 min), during which nothing
    raised, the 3-attempt cap never engaged, and the outbox silently rolled
    over via drop-oldest while `is_connected()` (the LISTENER's health, not
    the drain's) reported the pod as fine.

    MUTATION: drop `timeout=_EXECUTE_TIMEOUT` from `conn.execute(...)` in
    `_drain_outbox` ⇒ this fails — the fake's `asyncio.wait_for(...,
    timeout=None)` never times out, so the item is never dropped (bounded
    here by `_wait_until`'s own deadline, not a real hang).
    """
    monkeypatch.setattr(relay, "_EXECUTE_TIMEOUT", 0.05)
    monkeypatch.setattr(relay, "_DRAIN_RETRY_BACKOFF", (0,))
    conn = _RealisticHangingExecuteConnection()
    pool = _FakePool(conn, conn, conn)
    outbox: asyncio.Queue[str] = asyncio.Queue()
    outbox.put_nowait(relay._encode("task:log", {"task_id": "t1", "content": "x"}))

    start_count = relay._drain_dropped_count
    task = asyncio.create_task(relay._drain_outbox(pool, outbox))
    try:
        await _wait_until(lambda: relay._drain_dropped_count > start_count, deadline_seconds=3.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ── #804 round 8: the ownership rule, and what it fixes ─────────────────────


async def test_state_is_not_recreated_by_a_send_that_outlives_unregistration(
    monkeypatch,
) -> None:
    """#804 round 8 finding 3: `_send_or_skip`'s `setdefault` used to race
    `_unregister_client`'s `pop` — a wedged client with a STAGGERED in-flight
    send (started before unregistration, timing out AFTER it — e.g. another
    concurrent send for the same client unregistered it first) would
    recreate a `_SlowClientState` keyed by a dead websocket, leaking it
    forever and defeating the per-client dedupe for any future reuse of that
    object.

    MUTATION: remove the `if ws not in _clients: return` guard inside
    `_send_or_skip`'s `except TimeoutError` branch ⇒ this fails — the entry
    is recreated even though `ws` was unregistered before this send's own
    timeout fired.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    wedged = _WedgedWebSocket()
    events._register_client(wedged, None)

    disconnected: list[Any] = []
    send_task = asyncio.create_task(events._send_or_skip(wedged, "hello", disconnected))
    await asyncio.sleep(0)  # let send_task actually start awaiting send_text
    events._unregister_client(wedged)  # unregistered WHILE the send is in flight

    await send_task  # waits out `_SEND_TIMEOUT`, then hits the TimeoutError branch

    assert wedged not in events._slow_client_state, "state was recreated after unregistration"


# ── #804 round 7: `_slow_client_state`'s lifecycle, previously untested ─────


async def test_slow_client_state_is_cleaned_up_on_unregister() -> None:
    """#804 round 7: `_slow_client_state` had zero direct coverage anywhere
    despite being introduced in round 6 — its lifecycle (populated on
    timeout, cleaned up on unregister) was only ever exercised indirectly
    through `_send_or_skip`'s own behavior.

    MUTATION: remove the `_slow_client_state.pop(ws, None)` line from
    `_unregister_client` ⇒ this fails — the entry survives unregistration.
    """
    ws = _WedgedWebSocket()
    events._slow_client_state[ws] = events._SlowClientState(consecutive_timeouts=2, warned=True)

    events._unregister_client(ws)

    assert ws not in events._slow_client_state


async def test_unregistered_clients_slow_state_is_not_resurrected(monkeypatch) -> None:
    """A client that hit 2 consecutive timeouts, then disconnected (its
    entry cleaned up via `_unregister_client`), must not have those 2
    timeouts "remembered" if the same websocket object is registered again —
    a stale count could push a genuinely fresh client straight to eviction
    on its FIRST real timeout instead of its `_MAX_CONSECUTIVE_TIMEOUTS`th.

    MUTATION: change `_send_or_skip`'s `_slow_client_state.setdefault(ws,
    _SlowClientState())` to a bare `_slow_client_state[ws]` lookup with a
    module-level default created ONCE (so re-registration can find a
    lingering entry a cleanup somehow missed) ⇒ this fails on the second
    `consecutive_timeouts == 1` assertion — a fresh client's first timeout
    would read as its THIRD.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.05)
    wedged = _WedgedWebSocket()
    events._register_client(wedged, {"id": "user-a"})
    try:
        # Two timeouts — under the eviction threshold, still registered.
        await events._deliver_local_broadcast("task:test", {"x": 1})
        await events._deliver_local_broadcast("task:test", {"x": 1})
        assert events._slow_client_state[wedged].consecutive_timeouts == 2

        events._unregister_client(wedged)
        assert wedged not in events._slow_client_state

        # Re-register the SAME websocket object and time out ONCE — must
        # start counting from zero, not resume at 3 (which would evict this
        # otherwise-fresh client immediately on a single timeout).
        events._register_client(wedged, {"id": "user-a"})
        await events._deliver_local_broadcast("task:test", {"x": 1})

        assert wedged in events.active_connections, "stale count evicted a fresh client early"
        assert events._slow_client_state[wedged].consecutive_timeouts == 1
    finally:
        events._unregister_client(wedged)


async def test_a_single_stall_across_concurrent_emitters_does_not_evict(monkeypatch) -> None:
    """#804 round 8 finding 4: several emitters broadcasting concurrently
    (e.g. task:log, task:progress, ws:broadcast all firing during one burst)
    against the SAME wedged client during ONE stall must count as ONE stall
    window — not one increment per in-flight send. A naive per-timeout
    counter would reach `_MAX_CONSECUTIVE_TIMEOUTS` from this SINGLE stall
    event, which is exactly the eviction-on-one-stall behaviour round 5
    deliberately chose not to do when the send-timeout decision was reversed.

    MUTATION: remove the `if state.last_counted_at is None or now -
    state.last_counted_at >= _SEND_TIMEOUT:` guard in `_send_or_skip` (always
    increment on every timeout) ⇒ this fails — 3 concurrent broadcasts, each
    producing its own timeout for the SAME stall, would evict the client
    immediately instead of counting as one.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.2)
    wedged = _WedgedWebSocket()
    events._register_client(wedged, None)
    try:
        # 3 CONCURRENT emitters, all hitting the SAME wedged client during
        # the SAME stall.
        await asyncio.gather(
            events._deliver_local_broadcast("task:test", {"x": 1}),
            events._deliver_local_broadcast("task:test", {"x": 2}),
            events._deliver_local_broadcast("task:test", {"x": 3}),
        )

        assert wedged in events.active_connections, "a single stall must not evict"
        assert events._slow_client_state[wedged].consecutive_timeouts == 1
    finally:
        events._unregister_client(wedged)


class _ControllablePingWebSocket:
    """`send_text` distinguishes "pong" (always succeeds instantly, proving
    liveness) from any OTHER message (a real broadcast payload — hangs
    forever, modelling a stalled outbound delivery on the SAME socket).
    `receive_text` only returns "ping" when explicitly released via
    `release_ping()`, so a test can drive exactly ONE pong exchange at a
    precise point relative to other events."""

    def __init__(self) -> None:
        self.pongs: list[str] = []
        self._ping_ready = asyncio.Event()

    def release_ping(self) -> None:
        self._ping_ready.set()

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        await self._ping_ready.wait()
        self._ping_ready.clear()
        return "ping"

    async def send_text(self, message: str) -> None:
        if message == "pong":
            self.pongs.append(message)
            return
        await asyncio.sleep(3600)


async def test_a_client_that_pongs_between_stalls_is_not_evicted(monkeypatch) -> None:
    """#804 round 9 finding 3: `consecutive_timeouts` used to reset ONLY on
    a successful `_deliver_local_*` send — the pong reply (the definitive
    "this client is alive" signal, firing every 25s from the frontend) never
    touched `_slow_client_state` at all. That let two stalls during a burst,
    then an HOUR of idle pongs proving the client alive, then one more
    transient stall, evict it — inverting the "~15s of UNBROKEN silence" the
    threshold's own docstring promises into "3 stalls ever, unbounded in
    time".

    MUTATION: remove the `_slow_client_state.pop(websocket, None)` reset
    from `events_websocket`'s pong branch ⇒ this fails — the third stall
    below reaches the threshold (3, counting from the earlier 2) instead of
    starting over at 1, and the client gets evicted.
    """
    monkeypatch.setattr(events, "_SEND_TIMEOUT", 0.1)
    monkeypatch.setattr(events, "authenticate_websocket", AsyncMock(return_value=None))
    ws = _ControllablePingWebSocket()

    keepalive_task = asyncio.create_task(events.events_websocket(ws))
    await _wait_until(lambda: ws in events._clients, deadline_seconds=3.0)

    try:
        # Two stalls — under the eviction threshold.
        await events._deliver_local_broadcast("task:test", {"x": 1})
        await events._deliver_local_broadcast("task:test", {"x": 2})
        assert events._slow_client_state[ws].consecutive_timeouts == 2

        # A real pong exchange, driven by events_websocket's own loop.
        ws.release_ping()
        await _wait_until(lambda: len(ws.pongs) >= 1, deadline_seconds=3.0)
        assert ws not in events._slow_client_state, "a successful pong must reset the stall counter"

        # One more stall — must be treated as the FIRST of a new run, not
        # the third of the old one.
        await events._deliver_local_broadcast("task:test", {"x": 3})

        assert ws in events.active_connections, "a client that proved liveness was evicted anyway"
        assert events._slow_client_state[ws].consecutive_timeouts == 1
    finally:
        keepalive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive_task
        events._unregister_client(ws)


async def test_publish_drops_a_non_serializable_payload_without_raising(
    monkeypatch, caplog
) -> None:
    """#804 round 9 finding 5: `publish()`'s docstring says "never raises",
    but `_encode`'s first `json.dumps(payload)` had no guard — `data` is a
    free-form caller-supplied dict (`TaskProgress.data`, or any field on a
    model dumped via `asdict`), so the first caller to put a `Path`, a
    `datetime`, or a `set` in it raised `TypeError` straight out of
    `publish()`, through `_emit_progress`/`_emit_log`, into the stdout pump.

    MUTATION: remove the `try/except (TypeError, ValueError)` guard around
    `json.dumps(payload)` in `_encode` ⇒ this fails — `TypeError` propagates
    out of `publish()` instead of being caught, logged, and dropped.
    """
    monkeypatch.setattr(relay, "_resolve_asyncpg_url", lambda: "fake://url")
    monkeypatch.setattr(relay.asyncpg, "connect", AsyncMock(return_value=_FakeConnection([])))
    monkeypatch.setattr(relay.asyncpg, "create_pool", AsyncMock(return_value=_FakePool()))

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)

    non_serializable = {"task_id": "t1", "content": {1, 2, 3}}  # a set — not JSON-serializable

    with caplog.at_level(logging.WARNING, logger=relay.__name__):
        await relay.publish("task:log", non_serializable)  # must not raise

    matches = [r for r in caplog.records if "not JSON-serializable" in r.getMessage()]
    assert len(matches) == 1
    assert relay._outbox.empty(), "a non-serializable payload must not reach the outbox at all"
