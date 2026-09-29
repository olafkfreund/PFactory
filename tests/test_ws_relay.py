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
    listener would leak a running task into the next test.
    """
    yield
    await relay.stop_listener()
    relay._connection = None


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

    async def execute(self, *_args: Any) -> None:
        pass

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


# ── 1. publish is a no-op without a started relay ──────────────────────────


async def test_publish_without_connection_is_noop_and_does_not_raise(monkeypatch) -> None:
    """MUTATION: delete `if _connection is None: return` in publish() ⇒ this fails
    (`_encode` would get called, which this test asserts against, and the
    unconditional-execute mutation below would try to call `.execute` on
    `None` and get caught by publish's own broad `except Exception`, which is
    exactly why "does not raise" alone isn't enough here — see the second
    assertion).

    `_encode` is a plain SYNC function — using `Mock`, not `AsyncMock`, so a
    call would return the real encoded string rather than a coroutine object,
    the way a real call site would see it.
    """
    monkeypatch.setattr(relay, "_connection", None)
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


class _RaisingConnection:
    async def execute(self, *_args: Any) -> None:
        raise RuntimeError("boom: DB hiccup")

    async def close(self) -> None:
        pass


async def test_publish_failure_does_not_prevent_local_delivery_or_propagate(
    monkeypatch,
) -> None:
    """MUTATION: remove publish()'s try/except around `_connection.execute` ⇒
    this fails — `broadcast_event` would raise instead of swallowing the
    relay failure, even though local delivery already happened first.
    """
    ws = _FakeWebSocket()
    events.active_connections.add(ws)
    monkeypatch.setattr(relay, "_connection", _RaisingConnection())
    try:
        await events.broadcast_event("task:test", {"x": 1})  # must not raise
    finally:
        events.active_connections.discard(ws)

    assert ws.sent == [json.dumps({"type": "task:test", "payload": {"x": 1}})]


# ── 7. concurrent publish calls must not corrupt the shared connection ─────


class _ReentrancyDetectingConnection:
    """Raises the asyncpg InterfaceError symptom if two calls overlap.

    ``await asyncio.sleep`` inside ``execute`` yields control back to the
    event loop, so a second concurrently-gathered ``publish`` call WILL
    enter while the first is still "in flight" if nothing serialises them —
    this is deterministic, not a timing gamble.
    """

    def __init__(self) -> None:
        self.in_flight = False
        self.violation = False
        self.calls: list[str] = []

    async def execute(self, _sql: str, _channel: str, payload: str) -> None:
        if self.in_flight:
            self.violation = True
        self.in_flight = True
        await asyncio.sleep(0.01)
        self.calls.append(payload)
        self.in_flight = False

    async def close(self) -> None:
        pass


async def test_concurrent_publishes_are_serialised_and_all_land(monkeypatch) -> None:
    """MUTATION: delete `async with _publish_lock:` in publish() (dedent the
    `execute` call out from under it) ⇒ this fails — concurrent calls
    interleave inside `_ReentrancyDetectingConnection.execute` and
    `violation` becomes True.
    """
    conn = _ReentrancyDetectingConnection()
    monkeypatch.setattr(relay, "_connection", conn)

    await asyncio.gather(
        *[relay.publish("task:log", {"task_id": "t1", "content": f"line {i}"}) for i in range(10)]
    )

    assert conn.violation is False
    assert len(conn.calls) == 10
    # All ten distinct payloads actually reached "the DB", none lost.
    assert len({json.loads(c)["data"]["content"] for c in conn.calls}) == 10


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
    connect_mock = AsyncMock(side_effect=[conn1, conn2])
    monkeypatch.setattr(relay.asyncpg, "connect", connect_mock)

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is conn1)

    conn1.simulate_termination()  # fire_callback=True (default) — the fast path

    # The sentinel wakes the loop immediately; only `_RECONNECT_BACKOFF[0]`
    # (1s) stands between termination and the retry.
    await _wait_until(lambda: connect_mock.call_count >= 2, deadline_seconds=3.0)
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
    connect_mock = AsyncMock(side_effect=[conn1, conn2])
    monkeypatch.setattr(relay.asyncpg, "connect", connect_mock)
    monkeypatch.setattr(relay, "_POLL_INTERVAL", 0.3)  # keep the test fast

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is conn1)

    conn1.simulate_termination(fire_callback=False)  # the callback never fires

    # Reconnect now depends entirely on the poll backstop noticing
    # `is_closed()` on a `wait_for` timeout, plus `_RECONNECT_BACKOFF[0]`
    # (1s) before the retry.
    await _wait_until(lambda: connect_mock.call_count >= 2, deadline_seconds=5.0)
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
    connect_mock = AsyncMock(side_effect=[conn1, conn2])
    monkeypatch.setattr(relay.asyncpg, "connect", connect_mock)

    async def deliver(kind: str, data: dict) -> None:
        pass

    await relay.start_listener(deliver)
    await _wait_until(lambda: relay._connection is conn1)

    conn1.simulate_termination()

    await _wait_until(lambda: connect_mock.call_count >= 2, deadline_seconds=3.0)
    assert relay._connection is conn2


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
    # eventually see the good payload, but on a SECOND connection; this
    # asserts it happened on the ORIGINAL one, i.e. nothing tore down.
    assert delivered == [("task:log", {"marker": "good"})]
    assert relay._connection is fake_conn
