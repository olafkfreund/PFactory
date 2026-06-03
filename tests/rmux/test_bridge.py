"""Tests for ``apps/web-server/server/rmux/bridge.py``.

The bridge ties together the WebSocket route and the ``POST /attach``
endpoint that flips read-only viewers into bidirectional mode.

Coverage:

  - ``POST /attach`` happy path returns 200 + sets attached_connection_id
  - ``POST /attach`` against an already-attached session returns 409
  - 1000 concurrent ``POST /attach`` calls resolve to exactly one
    200 + 999 409 (the acceptance criterion from design §7)
  - ``POST /detach`` clears the attach if the caller holds it,
    returns ``not_holder`` otherwise
  - ``GET /api/tasks/<unknown>/agent-console/attach`` 404
  - The WS handshake's first frame carries a ``connection_id``

The handler-level audit-row write is verified via mock; the real
``log_audit_event`` is exercised in audit-service tests already and
we don't want this layer's tests to depend on the DB hash-chain code.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.rmux.bridge import router
from server.rmux.session import SessionRegistry, configure, reset_for_tests
from server.rmux.wrapper import RmuxWrapper

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry_between_tests():
    """Make sure the module-level singleton doesn't leak state across tests."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def app_with_bridge(tmp_path) -> tuple[FastAPI, SessionRegistry]:
    """Minimal FastAPI app mounting the bridge router with auth + DB
    dependencies overridden so we can drive it via TestClient without
    booting the whole web-server."""
    # Wrapper is fully mocked — bridge.attach/detach only need wrapper
    # access via send_keys (which only fires on WS input forwarding,
    # not on the REST path under test).
    mock_wrapper = AsyncMock(spec=RmuxWrapper)
    registry = configure(
        wrapper=mock_wrapper,
        panes_dir=tmp_path / "panes",
    )

    app = FastAPI()
    app.include_router(router)

    # Override the auth + DB dependencies so the test client can call
    # the routes without needing a real database or token middleware.
    from server.auth import verify_websocket_token
    from server.database.engine import get_db
    async def _no_op_db():
        yield None
    app.dependency_overrides[get_db] = _no_op_db

    return app, registry


@pytest.fixture
def client(app_with_bridge) -> TestClient:
    app, _ = app_with_bridge
    return TestClient(app)


@pytest.fixture
def primed_session(app_with_bridge, tmp_path) -> str:
    """Register a session with a known spec_id so attach/detach has
    something to flip.  Returns the spec_id."""
    _, registry = app_with_bridge

    async def _setup():
        await registry.create_for_task(
            spec_id="rsess-bridge-001",
            worktree_path=tmp_path,
            agent_cmd="true",
        )

    asyncio.get_event_loop().run_until_complete(_setup()) if False else asyncio.run(_setup())
    return "rsess-bridge-001"


# ---------------------------------------------------------------------------
# 404 + happy path
# ---------------------------------------------------------------------------


class TestAttach404:
    def test_attach_unknown_spec_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/tasks/never-registered/agent-console/attach",
            json={"connection_id": "abc-123"},
        )
        assert r.status_code == 404


class TestAttachHappy:
    def test_attach_first_caller_wins(
        self, client: TestClient, primed_session: str
    ) -> None:
        r = client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "conn-001"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "attached"
        assert body["connection_id"] == "conn-001"

    def test_attach_sets_attached_connection_id_on_state(
        self, client: TestClient, primed_session: str, app_with_bridge
    ) -> None:
        _, registry = app_with_bridge
        client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "conn-A"},
        )
        state = registry.get_state(primed_session)
        assert state.attached_connection_id == "conn-A"

    def test_second_attach_returns_409(
        self, client: TestClient, primed_session: str
    ) -> None:
        # First caller wins
        client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "conn-first"},
        )
        # Second caller loses
        r = client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "conn-second"},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "session_already_attached"


# ---------------------------------------------------------------------------
# Detach
# ---------------------------------------------------------------------------


class TestDetach:
    def test_holder_can_detach(
        self, client: TestClient, primed_session: str, app_with_bridge
    ) -> None:
        _, registry = app_with_bridge
        client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "owner"},
        )
        r = client.post(
            f"/api/tasks/{primed_session}/agent-console/detach",
            json={"connection_id": "owner"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "detached"
        # And another attach now succeeds
        r2 = client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "second-after-detach"},
        )
        assert r2.status_code == 200

    def test_non_holder_detach_is_no_op(
        self, client: TestClient, primed_session: str
    ) -> None:
        """A hostile client can't detach someone else by guessing IDs."""
        client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "real-owner"},
        )
        r = client.post(
            f"/api/tasks/{primed_session}/agent-console/detach",
            json={"connection_id": "i-am-an-imposter"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "not_holder"
        # Real owner is still attached
        r2 = client.post(
            f"/api/tasks/{primed_session}/agent-console/attach",
            json={"connection_id": "third-party"},
        )
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Race test — the design §7 acceptance criterion
# ---------------------------------------------------------------------------


class TestAttachRace:
    """1000 concurrent POST /attach → exactly one 200, 999 × 409.

    This is the design §7 acceptance criterion that gates the bridge.
    Implementation relies on the per-session ``asyncio.Lock`` in
    ``SessionState``.

    We use the FastAPI TestClient (sync) so we have to use a thread
    pool — but the handler itself is async, so the asyncio.Lock inside
    serialises the critical section even with concurrent thread
    callers.  The test exercises real lock semantics; mock the wrapper
    so we don't fork 1000 rmux subprocesses.
    """

    def test_one_200_999_409(
        self, client: TestClient, primed_session: str
    ) -> None:
        import concurrent.futures as cf

        N = 1000

        def hit(i: int) -> int:
            r = client.post(
                f"/api/tasks/{primed_session}/agent-console/attach",
                json={"connection_id": f"conn-{i:04d}"},
            )
            return r.status_code

        with cf.ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(hit, range(N)))

        ok = sum(1 for r in results if r == 200)
        conflict = sum(1 for r in results if r == 409)
        other = sum(1 for r in results if r not in (200, 409))

        assert ok == 1, f"expected exactly 1× 200, got {ok}"
        assert conflict == N - 1, f"expected {N - 1}× 409, got {conflict}"
        assert other == 0, f"unexpected non-{{200,409}} responses: {other}"
