"""P2.3 — credential-column migration + plaintext backfill.

Postgres-only. The migration uses ``op.batch_alter_table`` for SQLite
compatibility on simple cases but the multi-column drop+rename pattern
here is fragile under SQLite's table-copy semantics — the production
target is Postgres anyway, where ALTER COLUMN is native. Locally these
tests skip when ``TEST_POSTGRES_URL`` is unset; CI runs them as part of
the postgres-acceptance matrix.

Each test:
  1. Drops the public schema (clean slate).
  2. Sets KMS_FERNET_KEY + DATABASE_URL.
  3. Runs `alembic upgrade a4c2e9f8b1d3` (pre-P2.3 baseline + kms_data_keys).
  4. Seeds an EmailAccount row with a plaintext access_token via direct SQL.
  5. Runs `alembic upgrade head` to apply the P2.3 backfill.
  6. Asserts ciphertext on disk + plaintext round-trip via the ORM.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.secrets.helpers import WEB_SERVER_ROOT, reimport_crypto

# Required for these tests — set in CI's postgres-acceptance job.
TEST_POSTGRES_URL_ENV = "TEST_POSTGRES_URL"


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get(TEST_POSTGRES_URL_ENV)
    if not url:
        pytest.skip(
            f"{TEST_POSTGRES_URL_ENV} not set; P2.3 migration tests run "
            "against real Postgres only (CI postgres-acceptance matrix)"
        )
    return url


def _reset_schema(url: str) -> None:
    """Drop + recreate the public schema (idempotent test setup)."""
    async def _drop():
        eng = create_async_engine(url)
        try:
            async with eng.connect() as conn:
                await conn.execute(text("COMMIT"))
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
                await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
                await conn.commit()
        finally:
            await eng.dispose()
    asyncio.run(_drop())


def _run_alembic(target: str, url: str, fernet_key: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["KMS_FERNET_KEY"] = fernet_key
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=WEB_SERVER_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _seed_email_account(url: str, plaintext: str) -> str:
    """Insert one EmailAccount row with a plaintext access_token. Returns id."""
    # Sync URL for sync create_engine (the test URL is async-driver-prefixed).
    sync_url = url.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    owner_id = str(uuid.uuid4())
    row_id = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, email, name, password_hash, role, is_active) "
                "VALUES (:id, :em, 'O', 'x', 'admin', TRUE)"
            ), {"id": owner_id, "em": "owner@example.com"})
            conn.execute(text(
                "INSERT INTO email_accounts (id, user_id, provider, email_address, access_token) "
                "VALUES (:id, :uid, 'gmail', 'a@b.com', :tok)"
            ), {"id": row_id, "uid": owner_id, "tok": plaintext})
    finally:
        engine.dispose()
    return row_id


@pytest.mark.secrets
@pytest.mark.slow
def test_migration_backfills_plaintext_to_encrypted(fernet_key: str, pg_url: str) -> None:
    """P2.3 — plaintext seeded pre-migration is encrypted by `alembic upgrade head`."""
    _reset_schema(pg_url)
    plaintext = "ya29.A0AfH6SMBxxxxxxxxxxxxxxxx_FAKE_OAUTH_TOKEN"

    # Apply baseline + kms_data_keys (pre-P2.3 schema; access_token is TEXT).
    pre = _run_alembic("a4c2e9f8b1d3", pg_url, fernet_key)
    assert pre.returncode == 0, f"pre-P2.3 alembic failed:\n{pre.stderr[-1500:]}"

    row_id = _seed_email_account(pg_url, plaintext)

    # Apply P2.3.
    result = _run_alembic("head", pg_url, fernet_key)
    assert result.returncode == 0, f"P2.3 alembic failed:\n{result.stderr[-1500:]}"

    # Round-trip via the ORM — EncryptedString should decrypt back to plaintext.
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})
    from server.database.models import EmailAccount  # noqa: E402
    from sqlalchemy.orm import Session

    sync_url = pg_url.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    try:
        with Session(engine) as session:
            row = session.get(EmailAccount, row_id)
            assert row is not None, "row vanished after migration"
            assert row.access_token == plaintext, \
                f"ORM round-trip failed: {row.access_token!r} != {plaintext!r}"
    finally:
        engine.dispose()


@pytest.mark.secrets
@pytest.mark.slow
def test_pg_dump_contains_no_plaintext_credentials(fernet_key: str, pg_url: str) -> None:
    """P2.3 — after migration, raw column bytes contain NO plaintext."""
    _reset_schema(pg_url)
    plaintext = "secret-refresh-token-xyz789-uniquesentinel"

    pre = _run_alembic("a4c2e9f8b1d3", pg_url, fernet_key)
    assert pre.returncode == 0, f"pre-P2.3 alembic failed:\n{pre.stderr[-1500:]}"

    row_id = _seed_email_account(pg_url, plaintext)

    result = _run_alembic("head", pg_url, fernet_key)
    assert result.returncode == 0, f"P2.3 alembic failed:\n{result.stderr[-1500:]}"

    sync_url = pg_url.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT access_token FROM email_accounts WHERE id = :id"),
                {"id": row_id},
            ).scalar()
    finally:
        engine.dispose()

    # In Postgres LargeBinary maps to BYTEA → comes back as `memoryview` or bytes.
    assert isinstance(raw, (bytes, bytearray, memoryview)), \
        f"column type after migration should be bytes; got {type(raw).__name__}"
    assert plaintext.encode("utf-8") not in bytes(raw), \
        "plaintext leaked into the encrypted column after migration"
