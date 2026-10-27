"""Mint-time scope cap for ``POST /api/keys`` (#940)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.engine import get_db  # noqa: E402
from server.database.models import Base, Organization, OrgMember, User  # noqa: E402
from server.routes import api_keys  # noqa: E402
from server.routes.auth_routes import get_current_user  # noqa: E402


@pytest.fixture
def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    users: dict[str, User] = {}

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as s:
            for uid in ("viewer-u", "member-u"):
                u = User(id=uid, email=f"{uid}@x.test", password_hash="")
                s.add(u)
                users[uid] = u
            await s.flush()
            s.add(Organization(id="org-1", name="o", slug="o", owner_id="member-u"))
            await s.flush()
            s.add(OrgMember(org_id="org-1", user_id="viewer-u", role="viewer"))
            s.add(OrgMember(org_id="org-1", user_id="member-u", role="member"))
            await s.commit()

    asyncio.run(_init())

    app = FastAPI()
    app.include_router(api_keys.router)
    state = {"user": "member-u", "api_key": False}

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        if state["api_key"]:
            request.state.user = {"api_key_id": "k1"}
        return await call_next(request)

    async def _db():
        async with session_local() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: users[state["user"]]
    yield TestClient(app), state
    asyncio.run(engine.dispose())


def _mint(c, user, scopes, api_key=False):
    c, state = c
    state.update(user=user, api_key=api_key)
    return c.post("/api/keys", json={"name": "k", "org_id": "org-1", "scopes": scopes})


def test_viewer_cannot_mint_write(client):
    assert _mint(client, "viewer-u", ["mcp:write"]).status_code == 403


def test_viewer_cannot_mix_in_extra_scope(client):
    assert _mint(client, "viewer-u", ["mcp:read", "task:merge"]).status_code == 403


def test_viewer_can_mint_read(client):
    assert _mint(client, "viewer-u", ["mcp:read"]).status_code == 201


def test_member_can_mint_task_merge(client):
    assert _mint(client, "member-u", ["task:merge"]).status_code == 201


def test_api_key_principal_cannot_mint(client):
    assert _mint(client, "member-u", ["mcp:read"], api_key=True).status_code == 403
