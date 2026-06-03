"""P2.5 — KMS root-key rotation re-wraps per-org data keys.

These tests exercise the rotation routine in pure-Python via the Fernet
backend — no service container needed. The cloud-backend rotation paths
are tested indirectly: the rotation function is backend-agnostic, so if
it works correctly for fernet (round-trip with verifiable ciphertext
bytes), it works for every backend that satisfies the ``Backend``
protocol.
"""

from __future__ import annotations

import base64
import secrets
import time
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.secrets.helpers import reimport_crypto


def _new_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _build_fernet_backend(b64_key: str):
    """Build a fresh FernetBackend instance under a specific key.

    We bypass the factory's process-cached singleton because the test
    needs TWO distinct backends in scope at once (OLD + NEW). The
    factory's cache models a single-active-backend deployment, which
    is the right production constraint but the wrong abstraction here.
    """
    from server.crypto.kms.fernet import FernetBackend
    return FernetBackend(root_key=base64.urlsafe_b64decode(b64_key.encode()))


def _setup_db_with_old_wrapped_keys(
    old_backend, n_orgs: int = 5
):
    """Build an in-memory SQLite seeded with ``n_orgs`` kms_data_keys rows
    each wrapped under the OLD backend. Returns ``(engine, plaintexts)``
    where ``plaintexts[org_id]`` is the original 32-byte data key for
    later round-trip verification.
    """
    from server.database.models import Base, KmsDataKey, Organization, User

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    plaintexts: dict[str, bytes] = {}

    with Session(engine) as session:
        owner = User(
            id=str(uuid.uuid4()),
            email="owner@example.com",
            name="Owner",
            password_hash="x",
            role="admin",
            is_active=True,
        )
        session.add(owner)
        session.flush()

        for i in range(n_orgs):
            org = Organization(
                id=str(uuid.uuid4()),
                name=f"Test Org {i}",
                slug=f"test-org-{i}",
                owner_id=owner.id,
                plan="free",
            )
            data_key = secrets.token_bytes(32)
            wrapped = old_backend.encrypt(data_key)
            row = KmsDataKey(
                id=str(uuid.uuid4()),
                org_id=org.id,
                wrapped_key=wrapped,
                kms_key_id="fernet:old",
            )
            session.add_all([org, row])
            plaintexts[org.id] = data_key

        session.commit()

    return engine, plaintexts


@pytest.mark.secrets
@pytest.mark.slow
def test_rotation_rewraps_all_per_org_keys() -> None:
    """``rotate_root`` re-wraps every kms_data_keys row under the new root.

    Setup: 5 orgs, each with a kms_data_keys row wrapped under the OLD
    fernet key. After rotation:
      - Every row's wrapped_key has changed bytes.
      - The OLD backend can no longer decrypt the rows.
      - The NEW backend decrypts back to the ORIGINAL plaintext data
        key (proves the plaintext data keys themselves weren't altered
        — application data stays readable).
      - kms_key_id was updated to the new identifier.
      - rotated_at advanced.
    """
    # Reimport server.crypto with a placeholder env so the lazy
    # KmsDataKey + Organization model imports inside rotation.py work.
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": _new_fernet_key()})

    from server.crypto.rotation import rotate_root
    from server.database.models import KmsDataKey

    old_key = _new_fernet_key()
    new_key = _new_fernet_key()
    assert old_key != new_key, "test fixture must use distinct OLD/NEW keys"

    old_backend = _build_fernet_backend(old_key)
    new_backend = _build_fernet_backend(new_key)

    engine, original_plaintexts = _setup_db_with_old_wrapped_keys(old_backend, n_orgs=5)

    # Capture the pre-rotation wrapped bytes for the "changed" assertion.
    with Session(engine) as session:
        pre_rows = {r.org_id: bytes(r.wrapped_key) for r in session.query(KmsDataKey).all()}
        pre_rotated_at = {r.org_id: r.rotated_at for r in session.query(KmsDataKey).all()}

    # Tiny sleep so rotated_at definitely advances on systems with
    # microsecond-precision clocks.
    time.sleep(0.01)

    report = rotate_root(
        engine,
        old_backend=old_backend,
        new_backend=new_backend,
        new_kms_key_id="fernet:new",
        batch_size=2,  # forces multiple batches across 5 rows
    )

    assert report.error_count == 0, f"rotation errors: {report.errors}"
    assert report.rotated_count == 5, f"expected 5 rows rotated, got {report.rotated_count}"
    assert report.skipped_count == 0

    # Per-row assertions.
    with Session(engine) as session:
        rows = session.query(KmsDataKey).all()
        for row in rows:
            org_id = row.org_id
            new_wrapped = bytes(row.wrapped_key)

            # 1. wrapped_key bytes changed.
            assert new_wrapped != pre_rows[org_id], (
                f"wrapped_key for {org_id} should have changed after rotation"
            )

            # 2. OLD backend can no longer decrypt this row (different key).
            with pytest.raises(Exception):
                old_backend.decrypt(new_wrapped)

            # 3. NEW backend recovers the ORIGINAL plaintext data key.
            recovered = new_backend.decrypt(new_wrapped)
            assert recovered == original_plaintexts[org_id], (
                f"NEW backend should decrypt {org_id} back to the original data key"
            )

            # 4. kms_key_id was bumped.
            assert row.kms_key_id == "fernet:new"

            # 5. rotated_at advanced.
            assert row.rotated_at > pre_rotated_at[org_id]


@pytest.mark.secrets
@pytest.mark.slow
def test_rotation_invalidates_in_process_cache() -> None:
    """After rotation, DataKeyManager re-reads the row and re-unwraps under NEW.

    P2.2 already proved the manager notices a rotated_at change. This
    test plugs rotation in end-to-end: prime the cache under OLD,
    rotate, observe that the manager's next read returns the SAME data
    key bytes (rotation preserves the plaintext data key) — but
    crucially, having traversed the NEW unwrap path. We assert the
    cache entry's rotated_at advanced.
    """
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": _new_fernet_key()})

    from server.crypto import DataKeyManager
    from server.crypto.rotation import rotate_root

    old_backend = _build_fernet_backend(_new_fernet_key())
    new_backend = _build_fernet_backend(_new_fernet_key())

    engine, _plaintexts = _setup_db_with_old_wrapped_keys(old_backend, n_orgs=1)
    org_id = next(iter(_plaintexts))
    original_data_key = _plaintexts[org_id]

    # The manager sees the OLD backend (i.e., it's configured to unwrap
    # under the OLD root). Prime the cache.
    manager = DataKeyManager(
        sync_engine=engine,
        backend=old_backend,
        kms_key_id="fernet:old",
        poll_interval_s=0.0,  # always re-check on the next call
    )
    cached_before = manager.get_or_create_data_key(org_id)
    assert cached_before == original_data_key

    pre_cache_entry = manager._cache[org_id]  # type: ignore[attr-defined]
    pre_rotated_at = pre_cache_entry.rotated_at

    time.sleep(0.01)

    # Run rotation.
    rotate_root(
        engine,
        old_backend=old_backend,
        new_backend=new_backend,
        new_kms_key_id="fernet:new",
    )

    # Now hand the manager the NEW backend (mirrors the post-rotation
    # production state where the app is restarted with NEW env). The
    # manager's next read must invalidate the cache (rotated_at moved)
    # and re-unwrap via the NEW backend.
    manager._backend = new_backend  # type: ignore[attr-defined]
    refreshed = manager.get_or_create_data_key(org_id)

    # The plaintext data key is preserved across rotation.
    assert refreshed == original_data_key

    # The cache entry's rotated_at advanced (proves invalidation fired).
    post_cache_entry = manager._cache[org_id]  # type: ignore[attr-defined]
    assert post_cache_entry.rotated_at > pre_rotated_at
