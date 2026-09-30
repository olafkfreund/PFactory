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


# ── #804 round 6 finding 2: EVERY producer kind, not just ws:broadcast ──────
#
# The e2e test above proved `events.broadcast_event` -> `"ws:broadcast"` ->
# `_dispatch.dispatch` -> local delivery survives a real cross-process
# Postgres round trip. It proved NOTHING about the other four producer call
# sites (`agent_service._emit_log` -> `"task:log"`,
# `agent_service._emit_progress` -> `"task:progress"`,
# `events.send_to_user` -> `"ws:user"`, `events.send_to_org` -> `"ws:org"`) —
# reproduced by hand: renaming those four kinds at the producer
# (`"task:logs"` / `"task:progresss"` / etc.) left all 35 unit tests and both
# postgres tests above green, because nothing linked a producer's kind string
# to `_dispatch._HANDLERS` except this one file, and this file only covered
# one of the five. Cross-pod delivery of agent stdout and progress — this
# feature's primary purpose — could be completely dead with a fully green CI.
#
# MUTATION for each case below: rename that producer's kind string at its
# call site (e.g. `relay.publish("task:log", ...)` -> `relay.publish("task:logs", ...)`)
# ⇒ that one parametrized case must fail (checked by hand for all five; see
# the report accompanying this change for the per-case verified failure).

_E2E2_KINDS = ("task:log", "task:progress", "ws:user", "ws:org")

_E2E2_TASK_ID = "e2e-804-round6"
_E2E2_USER_ID = "e2e-804-user"
_E2E2_ORG_ID = "e2e-804-org"


def _e2e2_listener_worker(
    url: str, kind: str, ready: mp.synchronize.Event, results: mp.Queue
) -> None:
    """Pod B: register whatever local-delivery target `kind` uses (a task
    callback for task:log/task:progress, a fake locally-connected client for
    ws:user/ws:org), then run the REAL dispatch table.
    """
    os.environ["DATABASE_URL"] = url

    from server.services.agent_service import get_agent_service  # noqa: PLC0415

    captured: list[str] = []
    ws: _FakeWebSocket | None = None

    if kind == "task:log":
        get_agent_service().register_log_callback(
            _E2E2_TASK_ID, lambda log: captured.append(log.content)
        )
    elif kind == "task:progress":
        get_agent_service().register_progress_callback(
            _E2E2_TASK_ID, lambda progress: captured.append(progress.message)
        )
    elif kind == "ws:user":
        ws = _FakeWebSocket()
        events._register_client(ws, {"id": _E2E2_USER_ID})
    elif kind == "ws:org":
        ws = _FakeWebSocket()
        client = events._register_client(ws, {"id": "some-other-member"})
        client.org_ids = {_E2E2_ORG_ID}
    else:
        raise ValueError(kind)

    async def _run() -> None:
        await relay.start_listener(_dispatch.dispatch)
        await _wait_while(lambda: relay._connection is None, _TIMEOUT)
        await asyncio.sleep(0.5)
        ready.set()

        await _wait_while(lambda: not captured and not (ws and ws.sent), _TIMEOUT)
        await asyncio.sleep(1.0)  # grace window for a spurious second delivery
        await relay.stop_listener()

    asyncio.run(_run())
    results.put(("b", list(ws.sent) if ws is not None else captured))


def _e2e2_publisher_worker(
    url: str, kind: str, ready: mp.synchronize.Event, results: mp.Queue
) -> None:
    """Pod A: call the REAL production producer function for `kind` — never
    a raw `relay.publish`."""
    os.environ["DATABASE_URL"] = url

    from server.services.agent_service import (  # noqa: PLC0415
        TaskLog,
        TaskPhase,
        TaskProgress,
        get_agent_service,
    )

    async def _noop_deliver(_kind: str, _data: dict) -> None:
        pass

    async def _run() -> None:
        await relay.start_listener(_noop_deliver)  # only needed so publish() has a connection
        await _wait_while(lambda: relay._connection is None, _TIMEOUT)

        ready.wait(_TIMEOUT)

        if kind == "task:log":
            await get_agent_service()._emit_log(
                TaskLog(task_id=_E2E2_TASK_ID, content="hello log from a")
            )
        elif kind == "task:progress":
            await get_agent_service()._emit_progress(
                TaskProgress(
                    task_id=_E2E2_TASK_ID, phase=TaskPhase.CODING, message="hello progress from a"
                )
            )
        elif kind == "ws:user":
            await events.send_to_user(
                _E2E2_USER_ID, "task:e2e-804-user", {"marker": "hello-user-from-a"}
            )
        elif kind == "ws:org":
            await events.send_to_org(
                _E2E2_ORG_ID, "task:e2e-804-org", {"marker": "hello-org-from-a"}
            )
        else:
            raise ValueError(kind)

        await asyncio.sleep(1.5)
        await relay.stop_listener()

    asyncio.run(_run())
    results.put(("a", None))


@pytest.mark.parametrize("kind", _E2E2_KINDS)
def test_every_producer_kind_survives_a_real_cross_pod_round_trip(
    test_postgres_url: str, kind: str
) -> None:
    """Each of the four remaining producer call sites must reach real local
    delivery on another pod through the real `_dispatch.dispatch` table —
    see the module comment above for why `ws:broadcast` alone wasn't enough.
    """
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    results: mp.Queue = ctx.Queue()

    p_b = ctx.Process(target=_e2e2_listener_worker, args=(test_postgres_url, kind, ready, results))
    p_a = ctx.Process(target=_e2e2_publisher_worker, args=(test_postgres_url, kind, ready, results))
    p_b.start()
    p_a.start()
    try:
        out = dict(results.get(timeout=_TIMEOUT) for _ in range(2))
    finally:
        for p in (p_a, p_b):
            p.join(10)
            if p.is_alive():
                p.kill()

    received_b = out["b"]
    assert len(received_b) == 1, f"[{kind}] expected exactly one delivery to B, got {received_b}"

    if kind == "task:log":
        assert received_b[0] == "hello log from a"
    elif kind == "task:progress":
        assert received_b[0] == "hello progress from a"
    elif kind == "ws:user":
        message = json.loads(received_b[0])
        assert message == {"type": "task:e2e-804-user", "payload": {"marker": "hello-user-from-a"}}
    elif kind == "ws:org":
        message = json.loads(received_b[0])
        assert message == {"type": "task:e2e-804-org", "payload": {"marker": "hello-org-from-a"}}
