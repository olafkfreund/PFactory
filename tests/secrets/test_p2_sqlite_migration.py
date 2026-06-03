"""v3.0.1 regression — P2.3 encrypt_credentials migration works on SQLite.

v3.0.0 had a bug: the migration's "Step 5: re-apply NOT NULL" path
used `op.alter_column(... nullable=False)` directly, which SQLite
rejects (`ALTER TABLE ... ALTER COLUMN ... SET NOT NULL` is not
SQLite syntax). The fix wraps that step in `op.batch_alter_table`
so SQLite's table-copy semantics take effect.

This test runs `alembic upgrade head` against a fresh in-process
SQLite file and asserts every migration applies successfully. It
runs unconditionally (no Postgres needed) so the regression can't
silently regress on a Postgres-only CI matrix.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.secrets.helpers import WEB_SERVER_ROOT


@pytest.mark.secrets
@pytest.mark.slow
def test_alembic_upgrade_head_on_fresh_sqlite() -> None:
    """`alembic upgrade head` must apply ALL migrations against a
    freshly-created SQLite file. Regresses v3.0.0's encrypt_credentials
    failure (SET NOT NULL syntax rejected by SQLite)."""
    with tempfile.NamedTemporaryFile(
        suffix=".db", prefix="pfactory-v3.0.1-test-", delete=False
    ) as tmp:
        db_path = tmp.name
    try:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        # Pre-seed KMS env so any KMS-requiring migration path is happy
        # (the lazy-backend logic from P2.7 skips it for empty DBs, but
        # we set it anyway so the test doesn't depend on that path).
        env.setdefault(
            "KMS_FERNET_KEY",
            "dGVzdC1mZXJuZXQta2V5LWZvci10aGUtcmVncmVzc2lvbi10ZXN0cw==",
        )

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=WEB_SERVER_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, (
            f"alembic upgrade head failed on fresh SQLite "
            f"(exit {result.returncode}):\n"
            f"stdout:\n{result.stdout[-1500:]}\n\n"
            f"stderr:\n{result.stderr[-2000:]}"
        )
        # Sanity: all known migrations should appear in stdout in order.
        expected_revisions = [
            "1b386c99e615",  # baseline
            "a4c2e9f8b1d3",  # kms_data_keys
            "c6e3b2d4a8f0",  # encrypt_credentials  ← the v3.0.1 fix
            "d8f1a3c5e7b9",  # add oidc_sub
            "e9c4b6a2f8d1",  # oidc_refresh_sessions
            "f1c7b9d3a2e5",  # audit hardening
        ]
        combined = result.stdout + result.stderr
        for rev in expected_revisions:
            assert rev in combined, (
                f"migration {rev} did not appear in alembic output:\n"
                f"{combined[-1500:]}"
            )
    finally:
        Path(db_path).unlink(missing_ok=True)
