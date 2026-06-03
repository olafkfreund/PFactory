"""P1.1 — driver selection from DATABASE_URL scheme."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the web-server source importable for engine inspection.
_WEB_SERVER = Path(__file__).resolve().parents[2] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


def _reimport_engine(database_url: str):
    """Re-import `server.database.engine` with a fresh DATABASE_URL.

    Why we can't just `from server.database import engine as ...`:
      - engine.py builds its module-level Engine instance at import time
        from `os.environ["DATABASE_URL"]`. To pick up a new URL, the
        module must be re-executed.
      - `del sys.modules["server.database.engine"]` alone is not enough,
        because the parent package `server.database` retains the old
        engine module as an attribute — a subsequent `from server.database
        import engine` returns that cached reference.

    Solution: evict the engine submodule + the parent package, then use
    `importlib.import_module` which forces a fresh execution.
    """
    import importlib

    os.environ["DATABASE_URL"] = database_url
    sys.modules.pop("server.database.engine", None)
    sys.modules.pop("server.database", None)
    return importlib.import_module("server.database.engine")


@pytest.mark.postgres
@pytest.mark.slow
def test_engine_uses_asyncpg_for_postgres_url(test_postgres_url: str) -> None:
    """P1.1 — when DATABASE_URL is postgresql+asyncpg://..., the engine binds asyncpg."""
    original = os.environ.get("DATABASE_URL", "")
    try:
        engine_module = _reimport_engine(test_postgres_url)
        assert "asyncpg" in str(engine_module.engine.url), \
            f"engine URL did not select asyncpg: {engine_module.engine.url}"
        assert engine_module.engine.dialect.name == "postgresql", \
            f"dialect is {engine_module.engine.dialect.name}, expected postgresql"
    finally:
        os.environ["DATABASE_URL"] = original


@pytest.mark.postgres
def test_engine_keeps_aiosqlite_for_sqlite_url(tmp_path: Path) -> None:
    """P1.1 — when DATABASE_URL points at sqlite+aiosqlite://, the engine binds aiosqlite."""
    sqlite_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{sqlite_path}"
    original = os.environ.get("DATABASE_URL", "")
    try:
        engine_module = _reimport_engine(url)
        assert engine_module.engine.dialect.name == "sqlite", \
            f"dialect is {engine_module.engine.dialect.name}, expected sqlite"
    finally:
        os.environ["DATABASE_URL"] = original


@pytest.mark.postgres
def test_wal_listener_skipped_for_postgres(test_postgres_url: str) -> None:
    """P1.1 — the WAL-mode `connect` event hook must NOT fire on a Postgres engine."""
    original = os.environ.get("DATABASE_URL", "")
    try:
        engine_module = _reimport_engine(test_postgres_url)
        from sqlalchemy import event
        has_listener = (
            hasattr(engine_module, "_enable_wal_mode")
            and event.contains(
                engine_module.engine.sync_engine,
                "connect",
                engine_module._enable_wal_mode,
            )
        )
        assert not has_listener, "WAL listener registered on Postgres engine"
    finally:
        os.environ["DATABASE_URL"] = original
