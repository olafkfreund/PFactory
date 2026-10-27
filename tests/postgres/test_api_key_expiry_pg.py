"""#940: ``acw_`` key expiry and ``last_used_at`` against real Postgres.

asyncpg rejects tz-aware values for the naive ``DateTime`` columns, so the
mint (``expires_at``) and REST auth (``last_used_at``) paths must store naive
UTC. SQLite hides this; only Postgres proves it.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

pytest.importorskip("asyncpg")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import server.database.engine as engine_mod  # noqa: E402
from server import auth as auth_mod  # noqa: E402
from server.database.engine import get_db  # noqa: E402
from server.database.models import ApiKey, Base, Organization, OrgMember, User  # noqa: E402
from server.mcp_remote.auth import MCPAuthError, _lookup_by_digest  # noqa: E402
from server.routes import api_keys  # noqa: E402
from server.routes.auth_routes import get_current_user  # noqa: E402

SCHEMA = "apikey_expiry_pg"


def test_naive_utc_expiry_and_last_used_on_postgres(
    test_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(
        test_postgres_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup() -> User:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as s:
            user = User(id="u", email="u@x.test", password_hash="")
            s.add(user)
            await s.flush()
            s.add(Organization(id="org-1", name="o", slug="o", owner_id="u"))
            await s.flush()
            s.add(OrgMember(org_id="org-1", user_id="u", role="member"))
            await s.commit()
        return user

    async def _get_db():
        async with session_local() as s:
            yield s

    async def _row() -> ApiKey:
        async with session_local() as s:
            return (await s.execute(select(ApiKey))).scalar_one()

    async def _expire() -> None:
        async with session_local() as s:
            await s.execute(
                update(ApiKey).values(
                    expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
                )
            )
            await s.commit()

    async def _lookup(digest: str) -> None:
        async with session_local() as s:
            await _lookup_by_digest(s, digest)

    try:
        user = asyncio.run(_setup())

        app = FastAPI()
        app.include_router(api_keys.router)
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user] = lambda: user
        resp = TestClient(app).post(
            "/api/keys",
            json={"name": "k", "org_id": "org-1", "scopes": ["api"], "expires_in_days": 1},
        )
        assert resp.status_code == 201, resp.text
        raw = resp.json()["raw_key"]

        monkeypatch.setattr(engine_mod, "get_db", _get_db)
        principal = asyncio.run(auth_mod._try_authenticate_api_key(raw))
        assert principal is not None
        # The last_used write is best-effort and swallowed on failure: read it back.
        assert asyncio.run(_row()).last_used_at is not None

        asyncio.run(_expire())
        with pytest.raises(MCPAuthError, match="expired"):
            asyncio.run(_lookup(auth_mod._hash_api_key(raw)))
    finally:

        async def _drop() -> None:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await engine.dispose()

        asyncio.run(_drop())
