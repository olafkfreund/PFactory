"""Tests for the TestTargetCredential storage layer (#107, spec task 1).

Proves the new credential model encrypts its secret columns at rest, enforces
a unique (org_id, name), and that the API response shape never carries the
secret. Reuses the P2 EncryptedString fernet harness.
"""

from __future__ import annotations

import pytest

from tests.secrets.helpers import reimport_crypto


@pytest.mark.secrets
def test_model_columns_constraint_and_encrypted_type(fernet_key: str) -> None:
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})
    from server.database import TestTargetCredential
    from sqlalchemy import LargeBinary

    cols = TestTargetCredential.__table__.columns
    for name in (
        "id", "org_id", "name", "kind", "username",
        "secret", "extra", "created_by", "created_at", "last_used_at",
    ):
        assert name in cols, f"missing column {name}"

    # secret + extra must be encrypted-at-rest (EncryptedString impl is LargeBinary).
    for enc in ("secret", "extra"):
        col_type = cols[enc].type
        assert isinstance(col_type, LargeBinary) or hasattr(col_type, "impl"), (
            f"{enc} column must be encrypted-at-rest; got {col_type!r}"
        )

    # Unique (org_id, name) so .pfactory.yml refs are unambiguous.
    unique_col_sets = {
        tuple(sorted(c.name for c in con.columns))
        for con in TestTargetCredential.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("name", "org_id") in unique_col_sets


@pytest.mark.secrets
def test_secret_roundtrips_and_is_ciphertext_at_rest(fernet_key: str) -> None:
    reimport_crypto({"KMS_BACKEND": "fernet", "KMS_FERNET_KEY": fernet_key})
    from server.database import TestTargetCredential
    from sqlalchemy import create_engine, select

    engine = create_engine("sqlite:///:memory:")
    table = TestTargetCredential.__table__
    table.create(bind=engine)

    secret = "s3cr3t-pa$$w0rd-🔐"
    with engine.begin() as conn:
        conn.execute(
            table.insert(),
            {"id": "tc1", "org_id": "o1", "name": "login", "kind": "form", "secret": secret},
        )
        # Typed read decrypts transparently.
        got = conn.execute(select(table.c.secret)).first()
        assert got.secret == secret
        # Raw driver read bypasses the TypeDecorator → ciphertext bytes.
        raw = conn.exec_driver_sql("SELECT secret FROM test_target_credentials").fetchone()[0]

    assert isinstance(raw, (bytes, bytearray)), f"expected ciphertext bytes, got {type(raw)}"
    assert secret.encode("utf-8") not in bytes(raw), "plaintext secret leaked at rest"


@pytest.mark.secrets
def test_response_model_never_exposes_secret() -> None:
    from server.routes.target_credentials import TestCredentialResponse

    fields = set(TestCredentialResponse.model_fields)
    assert "secret" not in fields, "response model must not expose the secret"
    assert "extra" not in fields, "response model must not expose the extra blob"
    assert {
        "id", "org_id", "name", "kind", "username", "created_at", "last_used_at",
    } <= fields
