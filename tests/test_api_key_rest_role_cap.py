"""REST ``acw_`` key auth caps scopes by the owner's current org role (#940)."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from sqlalchemy import delete, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import server.database.engine as engine_mod  # noqa: E402
from server import auth as auth_mod  # noqa: E402
from server.database.models import (  # noqa: E402
    ApiKey,
    Base,
    Organization,
    OrgMember,
    User,
)

RAW = "acw_restcaptoken"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as s:
            s.add(User(id="u", email="u@x.test", password_hash=""))
            await s.flush()
            s.add(Organization(id="org-1", name="o", slug="o", owner_id="u"))
            await s.flush()
            s.add(OrgMember(org_id="org-1", user_id="u", role="member"))
            s.add(
                ApiKey(
                    user_id="u",
                    org_id="org-1",
                    key_hash="acw_rest$" + hashlib.sha256(RAW.encode()).hexdigest(),
                    name="k",
                    scopes="api",
                )
            )
            await s.commit()

    asyncio.run(_init())

    async def _get_db():
        async with session_local() as s:
            yield s

    monkeypatch.setattr(engine_mod, "get_db", _get_db)

    def _exec(stmt):
        async def _go():
            async with session_local() as s:
                await s.execute(stmt)
                await s.commit()

        asyncio.run(_go())

    yield _exec
    asyncio.run(engine.dispose())


def _auth():
    return asyncio.run(auth_mod._try_authenticate_api_key(RAW))


@pytest.mark.usefixtures("env")
def test_member_key_with_api_scope_authenticates():
    assert _auth()["api_key_id"]


def test_removed_member_key_is_rejected(env):
    env(delete(OrgMember).where(OrgMember.user_id == "u"))
    assert _auth() is None


def test_demoted_to_viewer_key_is_rejected(env):
    env(update(OrgMember).where(OrgMember.user_id == "u").values(role="viewer"))
    assert _auth() is None
