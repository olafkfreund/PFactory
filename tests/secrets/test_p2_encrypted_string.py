"""P2.1 — EncryptedString TypeDecorator (AES-256-GCM via Fernet backend)."""

import os

import pytest
from cryptography.exceptions import InvalidTag

from tests.secrets.helpers import reimport_crypto


def _make_table_with_encrypted_column():
    """Build a fresh in-memory SQLite table with an EncryptedString column.

    Returns ``(engine, table, EncryptedString)`` — caller drives the round-trip.
    """
    from server.crypto import EncryptedString
    from sqlalchemy import Column, Integer, MetaData, Table, create_engine

    metadata = MetaData()
    table = Table(
        "secrets_smoke",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("secret", EncryptedString(), nullable=False),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return engine, table, EncryptedString


@pytest.mark.secrets
def test_encrypted_string_roundtrip(fernet_key: str) -> None:
    """P2.1 — plaintext written via EncryptedString comes back identical on read."""
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})

    from sqlalchemy import select
    engine, table, _ = _make_table_with_encrypted_column()
    plaintext = "hunter2-very-secret-token-🔐"

    with engine.begin() as conn:
        conn.execute(table.insert(), {"id": 1, "secret": plaintext})
        row = conn.execute(select(table)).first()

    assert row.secret == plaintext, \
        f"round-trip mismatch: {row.secret!r} != {plaintext!r}"


@pytest.mark.secrets
def test_encrypted_string_rejects_tampered_ciphertext(fernet_key: str) -> None:
    """P2.1 — flipping a byte in the ciphertext raises InvalidTag on read."""
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})

    from sqlalchemy import select, update
    engine, table, _ = _make_table_with_encrypted_column()
    plaintext = "secret-payload"

    with engine.begin() as conn:
        conn.execute(table.insert(), {"id": 1, "secret": plaintext})

        # Tamper with the on-disk ciphertext bytes via direct SQL.
        # Flip bit 0 of the LAST byte (after nonce + most of ciphertext)
        # so the GCM tag verification fails on decrypt.
        from sqlalchemy import text as sql_text
        raw = conn.execute(sql_text("SELECT secret FROM secrets_smoke WHERE id=1")).scalar()
        tampered = bytes(raw[:-1] + bytes([raw[-1] ^ 0x01]))
        conn.execute(
            update(table).where(table.c.id == 1).values(secret_raw=tampered)
            if False else  # branch unused — we go via raw SQL to bypass TypeDecorator
            sql_text("UPDATE secrets_smoke SET secret = :v WHERE id = 1").bindparams(v=tampered)
        )

        # Reading should now raise InvalidTag from AESGCM.
        with pytest.raises(InvalidTag):
            conn.execute(select(table)).first()


@pytest.mark.secrets
def test_no_plaintext_in_stored_bytes(fernet_key: str) -> None:
    """P2.1 — the raw bytes stored in the column do NOT contain the plaintext.

    Same property `pg_dump` would observe in production: only ciphertext on
    disk, no plaintext leak. Verified here via direct-SQL read of the
    LargeBinary column (bypassing the TypeDecorator).
    """
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})

    from sqlalchemy import text as sql_text
    engine, table, _ = _make_table_with_encrypted_column()
    plaintext = "the-quick-brown-fox-jumps-over-the-lazy-dog"

    with engine.begin() as conn:
        conn.execute(table.insert(), {"id": 1, "secret": plaintext})
        raw = conn.execute(sql_text("SELECT secret FROM secrets_smoke WHERE id=1")).scalar()

    assert isinstance(raw, (bytes, bytearray)), \
        f"stored value is not bytes: {type(raw).__name__}"
    assert plaintext.encode("utf-8") not in bytes(raw), \
        "plaintext leaked into the encrypted column"
    # Ciphertext should also be longer than plaintext (nonce + GCM tag overhead = 28 bytes min).
    assert len(raw) >= len(plaintext) + 28, \
        f"stored bytes ({len(raw)}) shorter than expected nonce+tag+plaintext"
