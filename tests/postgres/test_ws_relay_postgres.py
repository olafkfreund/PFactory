"""Two real pods relay through one Postgres, and never hear their own echo (#804).

Each relay's origin (``relay._ORIGIN``) is derived from ``HOSTNAME`` plus a
per-process nonce computed once at import time. That's only genuinely
exercised across two separate ``spawn``ed processes — two calls in one
interpreter would share the same module globals (one ``_ORIGIN``, one
``_connection``), which is exactly what ``tests/test_ws_relay.py`` already
covers with a fake connection. This test is the one thing that can't be
faked: real ``LISTEN``/``NOTIFY`` across two real connections.

Marked ``postgres`` + ``slow``, like its neighbours in this directory.

MUTATION checked by hand for this test: delete the origin check in
``relay.py``'s listener loop ⇒ pod A's own publish would show up in A's
``received`` list too, and the ``out["a"] == []`` assertion would fail. (Not
re-verified by literally mutating and running against Postgres here — that
exact mutation is already proven, with a tighter and faster signal, by
``tests/test_ws_relay.py::test_listener_skips_own_origin_but_delivers_others``.
This test's OWN job — proving delivery survives a REAL Postgres round-trip
between two REAL processes — has no in-process substitute, which is why it
exists at all.)
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("asyncpg")

from server.websockets import _dispatch, events, relay  # noqa: E402

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

_TIMEOUT = 30


async def _wait_while(condition, deadline_seconds: float) -> None:
    """Poll a plain module-global / list for up to ``deadline_seconds``.

    Not an ``asyncio.Event``: what we're waiting on (``relay._connection``
    being set, a delivery landing in a plain ``list``) is set by relay.py's
    own background task, which exposes no event for either — adding one
    there for a test would be scope creep onto production code this task
    didn't ask for. This is a bounded poll, not a busy-wait: it always
    terminates at ``deadline_seconds``.
    """
    deadline = asyncio.get_event_loop().time() + deadline_seconds
    while condition() and asyncio.get_event_loop().time() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.1)


def _listener_worker(url: str, ready: mp.synchronize.Event, results: mp.Queue) -> None:
    """Pod B: start the relay, signal readiness, collect deliveries."""
    os.environ["DATABASE_URL"] = url

    received: list[tuple[str, dict]] = []

    async def deliver(kind: str, data: dict) -> None:
        received.append((kind, data))

    async def _run() -> None:
        await relay.start_listener(deliver)

        # start_listener only SCHEDULES the background connect+LISTEN; it
        # doesn't wait for it. Poll for the connection, then give the
        # LISTEN registration (a real round trip to Postgres) a moment to
        # land before telling pod A it's safe to publish.
        await _wait_while(lambda: relay._connection is None, _TIMEOUT)
        await asyncio.sleep(0.5)
        ready.set()

        await _wait_while(lambda: not received, _TIMEOUT)
        # Linger after the first delivery rather than stopping the instant it
        # arrives — a grace window to catch a SECOND, spurious delivery
        # (double delivery would otherwise be invisible: this loop would
        # just stop as soon as it saw the first one).
        await asyncio.sleep(1.0)
        await relay.stop_listener()

    asyncio.run(_run())
    results.put(("b", received))


def _publisher_worker(url: str, ready: mp.synchronize.Event, results: mp.Queue) -> None:
    """Pod A: start its own relay too (to prove it never hears its own echo),
    wait for B to be listening, then publish."""
    os.environ["DATABASE_URL"] = url

    received: list[tuple[str, dict]] = []

    async def deliver(kind: str, data: dict) -> None:
        received.append((kind, data))

    async def _run() -> None:
        await relay.start_listener(deliver)
        await _wait_while(lambda: relay._connection is None, _TIMEOUT)

        ready.wait(_TIMEOUT)
        await relay.publish("task:log", {"task_id": "cross-pod-804", "content": "hi from a"})

        # Give B time to actually receive it, and confirm nothing echoes
        # back to A in the meantime.
        await asyncio.sleep(1.5)
        await relay.stop_listener()

    asyncio.run(_run())
    results.put(("a", received))


def test_two_relays_one_database_deliver_only_to_the_other(test_postgres_url: str) -> None:
    """Publish from A; B's deliver receives it once; A's deliver is never called."""
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    results: mp.Queue = ctx.Queue()

    p_b = ctx.Process(target=_listener_worker, args=(test_postgres_url, ready, results))
    p_a = ctx.Process(target=_publisher_worker, args=(test_postgres_url, ready, results))
    p_b.start()
    p_a.start()
    try:
        out = dict(results.get(timeout=_TIMEOUT) for _ in range(2))
    finally:
        for p in (p_a, p_b):
            p.join(10)
            if p.is_alive():
                p.kill()

    assert out["a"] == [], "pod A must never receive its own publish"

    received_b = out["b"]
    assert len(received_b) == 1, f"expected exactly one delivery to B, got {received_b}"
    kind, data = received_b[0]
    assert kind == "task:log"
    assert data["task_id"] == "cross-pod-804"
    assert data["content"] == "hi from a"


# ── end-to-end: producer -> wire -> dispatch -> local delivery ─────────────
#
# Everything above wires `relay.start_listener` to a hand-written `deliver`
# closure that just appends to a list, and publishes with `relay.publish`
# directly. That never exercises `_dispatch.dispatch` or its `_HANDLERS`
# table at all, so a `kind` string typo on the producer side (`events.py` /
# `agent_service.py`) that doesn't match a `_HANDLERS` key — exactly what
# review findings 2 and 3 turned out to be, in a different form — would be
# invisible to every test above. This test wires the REAL production path on
# both ends: a real producer call (`events.broadcast_event`) on pod A, and
# the REAL `_dispatch.dispatch` (not a fake) as pod B's `deliver`, landing on
# a fake local websocket via `events.py`'s own local-delivery half.


class _FakeWebSocket:
    """A fake locally-connected client, registered directly in
    `events.active_connections` — the real local-delivery data structure,
    not a stand-in for it."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


def _e2e_listener_worker(url: str, ready: mp.synchronize.Event, results: mp.Queue) -> None:
    """Pod B: the REAL dispatch table, wired to a REAL local websocket."""
    os.environ["DATABASE_URL"] = url

    ws = _FakeWebSocket()
    events.active_connections.add(ws)

    async def _run() -> None:
        await relay.start_listener(_dispatch.dispatch)  # THE production dispatch table
        await _wait_while(lambda: relay._connection is None, _TIMEOUT)
        await asyncio.sleep(0.5)
        ready.set()

        await _wait_while(lambda: not ws.sent, _TIMEOUT)
        await asyncio.sleep(1.0)  # grace window for a spurious second delivery
        await relay.stop_listener()

    asyncio.run(_run())
    results.put(("b", list(ws.sent)))


def _e2e_publisher_worker(url: str, ready: mp.synchronize.Event, results: mp.Queue) -> None:
    """Pod A: a REAL producer call, not a raw `relay.publish`."""
    os.environ["DATABASE_URL"] = url

    async def _noop_deliver(_kind: str, _data: dict) -> None:
        pass

    async def _run() -> None:
        await relay.start_listener(_noop_deliver)  # only needed so publish() has a connection
        await _wait_while(lambda: relay._connection is None, _TIMEOUT)

        ready.wait(_TIMEOUT)
        await events.broadcast_event("task:e2e-804", {"marker": "hello-from-a"})

        await asyncio.sleep(1.5)
        await relay.stop_listener()

    asyncio.run(_run())
    results.put(("a", None))


def test_producer_to_wire_to_dispatch_to_local_delivery(test_postgres_url: str) -> None:
    """`events.broadcast_event` on A must reach a locally-connected client on
    B, through the real `_dispatch.dispatch` table — no test double standing
    in for the kind-string -> handler mapping.
    """
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    results: mp.Queue = ctx.Queue()

    p_b = ctx.Process(target=_e2e_listener_worker, args=(test_postgres_url, ready, results))
    p_a = ctx.Process(target=_e2e_publisher_worker, args=(test_postgres_url, ready, results))
    p_b.start()
    p_a.start()
    try:
        out = dict(results.get(timeout=_TIMEOUT) for _ in range(2))
    finally:
        for p in (p_a, p_b):
            p.join(10)
            if p.is_alive():
                p.kill()

    sent_b = out["b"]
    assert len(sent_b) == 1, f"expected exactly one message delivered to B's client, got {sent_b}"
    message = json.loads(sent_b[0])
    assert message == {"type": "task:e2e-804", "payload": {"marker": "hello-from-a"}}
