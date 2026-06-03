"""Tests for the web-server store: credential resolver (#107, task 4a).

Runs in the P2 `secrets` CI job (needs sqlalchemy async + aiosqlite +
cryptography). Verifies a stored credential decrypts on read, last_used_at is
bumped, and a missing id raises.
"""

from __future__ import annotations

import pytest

from tests.secrets.helpers import reimport_crypto


def test_parse_store_ref() -> None:
    from server.services.test_credential_resolver import parse_store_ref

    assert parse_store_ref("store:tc_1") == "tc_1"
    assert parse_store_ref("env:NAME") is None
    assert parse_store_ref("") is None


@pytest.mark.secrets
async def test_resolve_decrypts_and_bumps_last_used(fernet_key: str) -> None:
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})

    from server.database import TestTargetCredential
    from server.services.test_credential_resolver import (
        StoreCredentialNotFound,
        resolve_store_credential,
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TestTargetCredential.__table__.create)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(
            TestTargetCredential(
                id="tc_1", org_id="o1", name="login", kind="form",
                username="qa@acme.test", secret="s3cr3t-🔐",
            )
        )
        await db.commit()

        username, secret = await resolve_store_credential(db, "tc_1")
        assert username == "qa@acme.test"
        assert secret == "s3cr3t-🔐"  # decrypted on read

        row = await db.get(TestTargetCredential, "tc_1")
        assert row.last_used_at is not None  # bumped

        with pytest.raises(StoreCredentialNotFound):
            await resolve_store_credential(db, "nope")

    await engine.dispose()
