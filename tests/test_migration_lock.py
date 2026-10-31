"""SQLite boot migrations do not issue Postgres advisory lock SQL."""

import os
import subprocess
import sys
from pathlib import Path


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
