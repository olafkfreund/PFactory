"""MCP ``acw_`` key lookup against a real DB (#937).

Revoked, expired and deprovisioned-owner keys fail; a live key authenticates.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from sqlalchemy import delete, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import (  # noqa: E402
    ApiKey,
    Base,
    Organization,
    OrgMember,
    User,
)
from server.mcp_remote.auth import MCPAuthError, _lookup_by_digest  # noqa: E402

WRITE = frozenset({"mcp:read", "mcp:write"})


@pytest.fixture
def db(tmp_path):
    """Run ``db(fn)`` with a session on a per-test SQLite DB seeded with one org
    and one key, ``digest-u`` (scopes mcp:read,mcp:write), owned by member ``u``.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as s:
            s.add(User(id="u", email="u@x.test", password_hash=""))  # OIDC-style: no local password
            await s.flush()
            s.add(Organization(id="org-1", name="o", slug="o", owner_id="u"))
            await s.flush()
            s.add(OrgMember(org_id="org-1", user_id="u", role="member"))
            s.add(
                ApiKey(
                    user_id="u",
                    org_id="org-1",
                    key_hash="acw_xxxx$digest-u",
                    name="k",
                    scopes="mcp:read,mcp:write",
                )
            )
            await s.commit()

    asyncio.run(_init())

    def _run(fn):
        async def _go():
            async with session_local() as s:
                result = await fn(s)
                await s.commit()
                return result

        return asyncio.run(_go())

    yield _run
    asyncio.run(engine.dispose())


def _scopes(db):
    async def _fn(s):
        return (await _lookup_by_digest(s, "digest-u")).scopes

    return db(_fn)


def _exec(db, stmt):
    async def _fn(s):
        await s.execute(stmt)

    db(_fn)


def test_live_key_authenticates(db):
    assert _scopes(db) == WRITE


def test_revoked_key_fails(db):
    _exec(db, delete(ApiKey).where(ApiKey.user_id == "u"))
    with pytest.raises(MCPAuthError, match="Invalid API key"):
        _scopes(db)


def test_expired_key_fails(db):
    past = datetime.now(UTC) - timedelta(minutes=1)
    _exec(db, update(ApiKey).values(expires_at=past))
    with pytest.raises(MCPAuthError, match="expired"):
        _scopes(db)


def test_unexpired_key_still_authenticates(db):
    _exec(db, update(ApiKey).values(expires_at=datetime.now(UTC) + timedelta(days=1)))
    assert _scopes(db) == WRITE


def test_deactivated_owner_key_fails(db):
    # SCIM deprovisioning soft-deletes the user and leaves their keys in place.
    _exec(db, update(User).where(User.id == "u").values(is_active=False))
    with pytest.raises(MCPAuthError, match="deactivated"):
        _scopes(db)
