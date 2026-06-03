"""encrypt credential columns (forward-only)

P2.3 of Epic #26 — converts plaintext credential-bearing columns to
encrypted-at-rest LargeBinary via the EncryptedString TypeDecorator.

⚠ FORWARD-ONLY ⚠
Once this migration runs, the plaintext credentials are gone. The
downgrade() path raises NotImplementedError because reversing requires
the data to still be decryptable (which means having the KMS root key
+ the same data keys, both of which we deliberately don't preserve in
a downgrade). To downgrade past this revision: restore from a backup
taken BEFORE running this migration.

Operator preconditions:
  - KMS_FERNET_KEY (or APP_KMS_BACKEND-specific equivalents from P2.4)
    MUST be set BEFORE running `alembic upgrade head`. The migration
    instantiates the active KMS backend and uses it to encrypt rows
    in-place.
  - Take a `pg_dump` backup first. If the backfill fails partway
    through, restore from backup and retry.

Columns migrated (Text → LargeBinary via EncryptedString):
  email_accounts.access_token   (NOT NULL)
  email_accounts.refresh_token  (NULLABLE)
  llm_endpoints.api_key         (NULLABLE)

Revision ID: c6e3b2d4a8f0
Revises: a4c2e9f8b1d3
Create Date: 2026-05-25

"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Make `server.crypto` importable from inside the migration. env.py
# adds apps/web-server to sys.path; this is a defensive no-op for
# direct-invocation paths.
_WEB_SERVER = Path(__file__).resolve().parents[3]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


# revision identifiers, used by Alembic.
revision: str = "c6e3b2d4a8f0"
down_revision: Union[str, Sequence[str], None] = "a4c2e9f8b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Columns to migrate: (table, column, nullable).
# ``nullable`` matches the model definition in models.py — True means the
# column allows NULL, False means it doesn't and we re-apply NOT NULL after
# the backfill.
_CRED_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("email_accounts", "access_token",  False),  # NOT NULL in model
    ("email_accounts", "refresh_token", True),   # nullable in model
    ("llm_endpoints",  "api_key",       True),   # nullable in model
)


def _backend():
    """Resolve the active KMS backend lazily so import doesn't fail when
    KMS env vars aren't set (e.g. during downgrade or test collection)."""
    from server.crypto.kms import get_backend
    return get_backend()


def upgrade() -> None:
    """Forward-only credential encryption.

    For each (table, col, not_nullable):
      1. Add a temp `<col>_ct` LargeBinary column (nullable).
      2. Backfill: read plaintext, encrypt, write to temp.
      3. Drop the original column.
      4. Rename the temp column to the original name.
      5. If NOT NULL: apply the constraint after backfill is complete.

    Direct DDL (no ``batch_alter_table``) — these migrations target
    Postgres only. SQLite users hit the simpler boot path that bypasses
    Alembic entirely (``create_all`` from models).
    """
    conn = op.get_bind()
    # The KMS backend is resolved lazily — only when there's actually
    # plaintext to encrypt. A fresh-install upgrade against an empty
    # database doesn't touch credentials, so it shouldn't require
    # KMS_FERNET_KEY (or the equivalent cloud env vars) to be wired.
    # The P2.6 runbook documents the env-var requirement for backfill
    # runs against real plaintext.
    backend = None

    for table, col, nullable in _CRED_COLUMNS:
        temp_col = f"{col}_ct"

        # Step 1: add temp column (always nullable so the add itself can't fail).
        op.add_column(table, sa.Column(temp_col, sa.LargeBinary(), nullable=True))

        # Step 2: backfill plaintext → ciphertext (only if rows exist).
        rows = conn.execute(
            sa.text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
        ).fetchall()
        if rows and backend is None:
            backend = _backend()
        for row in rows:
            ciphertext = backend.encrypt(row[1].encode("utf-8"))
            conn.execute(
                sa.text(f"UPDATE {table} SET {temp_col} = :ct WHERE id = :id"),
                {"ct": ciphertext, "id": row[0]},
            )

        # Step 3: drop the original plaintext column.
        op.drop_column(table, col)

        # Step 4: rename the temp column to the original name via raw DDL.
        # ``op.alter_column(..., new_column_name=...)`` on Postgres has a
        # quirk where it emits an unintended ``SET NOT NULL`` even when
        # ``existing_nullable=True`` is passed. Raw ``RENAME COLUMN`` is
        # both simpler and guaranteed not to change nullability.
        op.execute(f'ALTER TABLE {table} RENAME COLUMN {temp_col} TO {col}')

        # Step 5: re-apply NOT NULL where the schema requires it. Safe because
        # Step 2 backfilled every row that wasn't already NULL — and if a row
        # was originally NULL on a NOT NULL column the original schema was
        # already inconsistent and we want this to fail loudly.
        #
        # v3.0.1: wrap in batch_alter_table for SQLite portability. SQLite
        # doesn't support `ALTER TABLE ... ALTER COLUMN ... SET NOT NULL`
        # via plain ALTER; batch mode re-creates the table atomically with
        # the new NOT NULL constraint. On Postgres the batch reduces to a
        # native ALTER COLUMN. Same pattern P3.3's d8f1a3c5e7b9 uses.
        if not nullable:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(
                    col,
                    existing_type=sa.LargeBinary(),
                    nullable=False,
                )


def downgrade() -> None:
    """Downgrading past this revision is NOT supported.

    Reversing requires decrypting every column, which would silently
    expose all credentials in plaintext on disk if the KMS root key
    were ever compromised after the downgrade. To downgrade past P2.3,
    restore from a pre-migration backup.
    """
    raise NotImplementedError(
        "Downgrading past P2.3 (c6e3b2d4a8f0) is forward-only. "
        "Restore from a pre-migration backup."
    )
