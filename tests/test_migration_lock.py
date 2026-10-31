"""SQLite boot migrations do not issue Postgres advisory lock SQL."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from server.database import engine as engine_mod


def test_sqlite_init_db_does_not_use_advisory_lock(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}",
            "APP_MIGRATIONS_AUTO_APPLY": "true",
            "APP_DISABLE_AUTH": "true",
            "GRAPHITI_ENABLED": "false",
        }
    )
    code = """
import asyncio
import sys
from sqlalchemy import event
sys.path.insert(0, 'apps/web-server')
from server.database.engine import engine, init_db
event.listen(engine.sync_engine, 'before_cursor_execute',
             lambda conn, cursor, statement, parameters, context, executemany: print(statement))
asyncio.run(init_db())
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "PRAGMA journal_mode" in result.stdout
    assert "pg_advisory" not in result.stdout.lower()


def test_postgres_upgrade_runs_inside_lock_with_timeout(monkeypatch) -> None:
    """The upgrade runs between SET lock_timeout + pg_advisory_lock and unlock."""
    calls: list[str] = []

    class _Conn:
        async def execution_options(self, **_kw):
            return self

        async def execute(self, stmt, _params=None):
            calls.append(str(stmt))

    class _Engine:
        def connect(self):
            conn = _Conn()

            class _Ctx:
                async def __aenter__(self):
                    return conn

                async def __aexit__(self, *_exc):
                    return False

            return _Ctx()

        async def dispose(self):
            calls.append("dispose")

    monkeypatch.setattr(engine_mod, "create_async_engine", lambda *_a, **_kw: _Engine())
    monkeypatch.setattr(engine_mod, "_alembic_upgrade_head_sync", lambda: calls.append("upgrade"))

    asyncio.run(engine_mod._upgrade_head_with_pg_lock())

    assert calls == [
        "SET lock_timeout = '300s'",
        "SELECT pg_advisory_lock(:k)",
        "upgrade",
        "SELECT pg_advisory_unlock(:k)",
        "dispose",
    ]
