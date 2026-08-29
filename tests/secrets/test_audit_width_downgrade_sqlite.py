"""The b8e1f4c7a2d9 downgrade must run on SQLite, not just on Postgres.

The migration widens ``audit_logs.resource_id`` and wraps both directions in
``batch_alter_table`` precisely so SQLite -- which has no ALTER COLUMN -- can
apply it. But the downgrade's own safety check called ``char_length()``, which
is a Postgres function SQLite does not have. So the upgrade was portable and
the downgrade was not: a rollback on the SQLite the test suite migrates against
died with "no such function: char_length" BEFORE the guard could report
anything, and the portability the batch wrapper exists for stopped one line
short.

``length()`` is ANSI and exists on both engines, with the same semantics for
this use (character count of a text value).

This exercises the real ``alembic downgrade``, not the SQL string: a test that
grepped the migration for ``length(`` would pass on ``char_length(`` too.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.secrets.helpers import WEB_SERVER_ROOT

_ENV_KMS = "dGVzdC1mZXJuZXQta2V5LWZvci10aGUtcmVncmVzc2lvbi10ZXN0cw=="


def _alembic(args: list[str], db_path: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    env.setdefault("KMS_FERNET_KEY", _ENV_KMS)
    # S603: the argv is this module's own literals plus a revision id defined
    # above -- no caller and no external input reaches it.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", *args],
        cwd=WEB_SERVER_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.secrets
@pytest.mark.slow
def test_audit_resource_id_downgrade_runs_on_sqlite() -> None:
    """upgrade head -> downgrade to b8e1f4c7a2d9's parent -> upgrade head again."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        up = _alembic(["upgrade", "head"], db_path)
        assert up.returncode == 0, f"upgrade head failed:\n{up.stderr[-2000:]}"

        down = _alembic(["downgrade", "a7d2e4b8c1f3"], db_path)
        combined = down.stdout + down.stderr
        assert "char_length" not in combined, (
            f"the downgrade called char_length(), which SQLite does not have:\n{combined[-2000:]}"
        )
        assert down.returncode == 0, (
            f"downgrade of b8e1f4c7a2d9 failed on SQLite:\n{combined[-2000:]}"
        )

        # The guard must have actually evaluated (empty table => 0 too-long
        # rows => it proceeds). Prove the column really narrowed, so a
        # downgrade that silently no-opped cannot pass as a working one --
        # which is exactly what the first draft of this test did, by
        # downgrading TO b8e1f4c7a2d9 (leaving it applied) instead of to its
        # parent. Without this assertion that draft passed.
        conn = sqlite3.connect(db_path)
        try:
            cols = {
                row[1]: row[2] for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()
            }
        finally:
            conn.close()
        assert cols["resource_id"] == "VARCHAR(36)", (
            f"downgrade reported success but resource_id is {cols['resource_id']}"
        )

        again = _alembic(["upgrade", "head"], db_path)
        assert again.returncode == 0, f"re-upgrade after downgrade failed:\n{again.stderr[-2000:]}"
    finally:
        Path(db_path).unlink(missing_ok=True)
