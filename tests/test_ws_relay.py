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
from typing import Any
from unittest.mock import AsyncMock

import pytest

from server.services.agent_service import get_agent_service
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
    """

    def __init__(self, notifications: list[tuple[str, str]]) -> None:
        self._notifications = notifications
        self.closed = False

    async def add_listener(self, _channel: str, callback: Any) -> None:
        for channel, payload in self._notifications:
            callback(self, 0, channel, payload)

    async def remove_listener(self, _channel: str, _callback: Any) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def execute(self, *_args: Any) -> None:
        pass


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
    (_encode would get called, which this test asserts against)."""
    monkeypatch.setattr(relay, "_connection", None)
    encode_spy = AsyncMock(wraps=relay._encode)
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
    """MUTATION: delete `truncated_data["truncated"] = True` in _encode() ⇒ this
    fails — the decoded payload would fit under the cap but lack the flag."""
    data = {"task_id": "t1", "content": "x" * 20_000}

    encoded = relay._encode("task:log", data)

    assert encoded is not None
    assert len(encoded.encode()) <= relay._MAX_PAYLOAD
    decoded = json.loads(encoded)
    assert decoded["data"]["truncated"] is True
    assert len(decoded["data"]["content"]) < 20_000


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
    """
    captured: list[Any] = []

    async def _fake_deliver_local_progress(progress: Any) -> None:
        captured.append(progress)

    monkeypatch.setattr(
        get_agent_service(), "_deliver_local_progress", _fake_deliver_local_progress
    )

    data = {
        "task_id": "t1",
        "phase": "coding",
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

    await _dispatch.dispatch("task:progress", data)

    assert len(captured) == 1
    assert captured[0].phase.value == "coding"
