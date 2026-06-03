"""Pytest fixtures for P5 audit-hardening acceptance tests.

Tests are marked ``@pytest.mark.audit``. They run entirely in-process
against an in-memory SQLite — no service containers needed (audit
code is pure-Python: hash chain on write, retention job, GDPR
erasure, export streaming).

The fixtures here build a per-test app + DB (mirroring tests/oidc's
pattern) so each test exercises a clean audit log.
"""

from __future__ import annotations

import asyncio
import secrets as _test_secrets
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SERVER_ROOT = REPO_ROOT / "apps" / "web-server"

# Re-exported for tests that need to set PYTHONPATH on subprocess.run().
__all__ = ["fresh_db", "WEB_SERVER_ROOT"]

if str(WEB_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_ROOT))


@pytest.fixture
def fresh_db():
    """Build a fresh in-memory async SQLite + Base.metadata.create_all.

    Returns ``(engine, SessionLocal)``. Per-test isolation via a
    unique in-memory DB name nonce (see tests/oidc for the bug we're
    avoiding).
    """
    from server.database.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    nonce = _test_secrets.token_hex(8)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:p5test-{nonce}?mode=memory&cache=shared&uri=true"
    )

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_init())
    finally:
        loop.close()

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, SessionLocal
