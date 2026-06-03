"""P2.2 — kms_data_keys table + per-org data key generation + cache."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.secrets.helpers import reimport_crypto


def _setup_db(fernet_key: str):
    """Build an in-memory SQLite with the org + kms_data_keys schema.

    Returns ``(engine, organization_id, manager)``. We bypass Alembic and
    use `Base.metadata.create_all()` for tests because the migration is
    Postgres-flavored but the models are dialect-portable; create_all
    produces the same logical schema on SQLite.
    """
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})

    from server.crypto import DataKeyManager, get_backend
    from server.database.models import Base, Organization, User

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # Seed the minimum rows for the FK to resolve.
    with Session(engine) as session:
        owner = User(
            id=str(uuid.uuid4()),
            email="owner@example.com",
            name="Owner",
            password_hash="x",
            role="admin",
            is_active=True,
        )
        org = Organization(
            id=str(uuid.uuid4()),
            name="Test Org",
            slug="test-org",
            owner_id=owner.id,
            plan="free",
        )
        session.add_all([owner, org])
        session.commit()
        org_id = org.id

    manager = DataKeyManager(
        sync_engine=engine,
        backend=get_backend(),
        kms_key_id="fernet:test",
        poll_interval_s=0.0,  # poll on every call so the rotation test sees changes immediately
    )
    return engine, org_id, manager


@pytest.mark.secrets
def test_kms_data_key_created_on_first_use(fernet_key: str) -> None:
    """P2.2 — first call for an org creates exactly one kms_data_keys row.

    Subsequent calls return the SAME key (no second row).
    """
    engine, org_id, manager = _setup_db(fernet_key)

    key1 = manager.get_or_create_data_key(org_id)
    assert len(key1) == 32, "expected 32-byte AES-256 data key"

    # Second call returns the same in-process cached key.
    key2 = manager.get_or_create_data_key(org_id)
    assert key2 == key1, "second call should reuse the cached key"

    # And there's exactly ONE row in kms_data_keys.
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM kms_data_keys WHERE org_id = :o"
        ), {"o": org_id}).scalar()
    assert n == 1, f"expected 1 kms_data_keys row, found {n}"


@pytest.mark.secrets
def test_lru_cache_evicts_on_rotation(fernet_key: str) -> None:
    """P2.2 — when kms_data_keys.rotated_at advances, the cache re-fetches.

    Simulates a root-key rotation: row's `wrapped_key` is re-wrapped under
    a different envelope and `rotated_at` bumped. Manager must notice and
    re-unwrap (yielding the same plaintext data key, but proving the
    polling check fired).
    """
    engine, org_id, manager = _setup_db(fernet_key)

    # Prime the cache.
    original = manager.get_or_create_data_key(org_id)

    # Simulate rotation: bump rotated_at directly. Same wrapped_key so the
    # plaintext data key bytes don't actually change — this isolates "did
    # the cache notice the timestamp change?" from key-bytes change.
    new_rotated_at = datetime.utcnow() + timedelta(seconds=10)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE kms_data_keys SET rotated_at = :t WHERE org_id = :o"),
            {"t": new_rotated_at, "o": org_id},
        )

    # Tiny sleep so monotonic() advances past poll_interval_s=0.
    time.sleep(0.01)

    # Trigger the cache path. Internally it should observe the rotated_at
    # change, evict the cached entry, and re-unwrap. Result equals original
    # bytes (same wrapped_key) but the manager's cache row is new.
    refreshed = manager.get_or_create_data_key(org_id)
    assert refreshed == original, "data key bytes should be stable"

    # Inspect manager state: the cached_at should be very recent (just refreshed).
    assert org_id in manager._cache  # type: ignore[attr-defined]
    cached_rotated_at = manager._cache[org_id].rotated_at  # type: ignore[attr-defined]
    assert cached_rotated_at == new_rotated_at, \
        "cache should now hold the new rotated_at timestamp"


@pytest.mark.secrets
def test_data_key_isolation_between_orgs(fernet_key: str) -> None:
    """P2.2 — Org A and Org B get DIFFERENT data keys.

    Encrypting under Org A's key and trying to decrypt with Org B's key
    must fail (or, equivalently, the two keys must not be equal — which
    is the cheap check).
    """
    engine, org_a_id, manager = _setup_db(fernet_key)

    # Add a second org sharing the same owner.
    from server.database.models import Organization
    org_b_id = str(uuid.uuid4())
    with Session(engine) as session:
        # Reuse the owner from _setup_db (the only User in the DB).
        owner_id = session.execute(text("SELECT id FROM users LIMIT 1")).scalar()
        session.add(Organization(
            id=org_b_id, name="Org B", slug="org-b", owner_id=owner_id, plan="free",
        ))
        session.commit()

    key_a = manager.get_or_create_data_key(org_a_id)
    key_b = manager.get_or_create_data_key(org_b_id)

    assert key_a != key_b, "different orgs must get different data keys"

    # Stronger assertion: encrypting with A and decrypting with B fails.
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = b"\x00" * 12
    ciphertext = AESGCM(key_a).encrypt(nonce, b"secret-from-A", associated_data=None)
    with pytest.raises(InvalidTag):
        AESGCM(key_b).decrypt(nonce, ciphertext, associated_data=None)
