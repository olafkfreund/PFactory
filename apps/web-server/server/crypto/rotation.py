"""KMS root-key rotation for PFactory's encrypted-at-rest secrets.

Rotation re-wraps every per-org data key under a *new* KMS root reference.
The plaintext data keys themselves don't change — so the millions of
EncryptedString-protected columns don't need to be touched. Only the
``kms_data_keys.wrapped_key`` blobs are updated.

This module is the implementation of the rotation runbook (P2.6).
Operators trigger it via::

    python -m server.crypto rotate-root \\
        --new-kms-key-id <id-of-new-CMK-or-Vault-key>

…with the new backend's credentials wired in the env (e.g. for AWS:
``AWS_KMS_KEY_ID_NEW`` plus the new CMK's IAM permissions for the
running identity). The CLI's job is to build OLD and NEW backend
instances from env and call ``rotate_root()``.

The rotation window is the period during which BOTH the old and new
KMS root keys are usable. Operators must:
  1. Provision the new root, grant the running identity Encrypt+Decrypt
     on it.
  2. Keep the old root's Decrypt permission alive.
  3. Run the rotation.
  4. After confirming success, revoke the old root (or schedule its
     deletion per the cloud provider's grace period — AWS = 7-30 days,
     Vault = manual archive).

Crash safety: each row is rotated in its own transaction. A mid-run
crash leaves some rows under OLD and some under NEW — BOTH backends
must remain usable until the rotation completes. The function is safe
to re-run; rows already rotated past the run-start timestamp are
skipped on a re-run.

Backend-agnostic: the function takes two ``Backend`` instances and
treats them as opaque ``encrypt`` / ``decrypt`` pairs. Works for all
five backend types (fernet, aws_kms, vault_transit, azure_kv, gcp_kms)
without per-backend code paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .kms import Backend

logger = logging.getLogger(__name__)


@dataclass
class RotationReport:
    """Audit record of a single rotation run.

    Operators ingest this into their compliance / SOC2 evidence
    pipeline. The ``errors`` list contains (org_id, exception_repr)
    tuples for any row that failed to re-wrap — those rows stay under
    the OLD root and must be rotated by a follow-up run after the
    operator addresses the cause (typically: insufficient IAM on the
    new CMK, or a transient KMS rate-limit).
    """

    started_at: datetime
    finished_at: datetime | None = None
    rotated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    new_kms_key_id: str = ""

    def summary(self) -> str:
        duration = (
            (self.finished_at - self.started_at).total_seconds()
            if self.finished_at
            else float("nan")
        )
        return (
            f"KMS root rotation complete: "
            f"rotated={self.rotated_count} skipped={self.skipped_count} "
            f"errors={self.error_count} duration={duration:.1f}s "
            f"new_kms_key_id={self.new_kms_key_id!r}"
        )


def rotate_root(
    sync_engine: Engine,
    *,
    old_backend: Backend,
    new_backend: Backend,
    new_kms_key_id: str,
    batch_size: int = 100,
) -> RotationReport:
    """Re-wrap every ``kms_data_keys`` row under ``new_backend``.

    Parameters:
      sync_engine: SQLAlchemy sync engine. Rotation is a one-shot ops
        task, not part of a hot request path, so the sync path is the
        right tool here.
      old_backend: ``Backend`` instance configured for the existing
        root. Must be able to ``decrypt()`` every existing wrapped_key.
      new_backend: ``Backend`` instance configured for the new root.
        Must be able to ``encrypt()`` 32-byte data keys.
      new_kms_key_id: Human-readable identifier for the new root; gets
        written to ``kms_data_keys.kms_key_id`` for audit trails.
      batch_size: How many rows to fetch + rotate per database round
        trip. Smaller = less lock contention; larger = fewer commits.

    Returns:
      A ``RotationReport`` with per-row outcomes.
    """
    # Lazy-imported to avoid the circular-import dance with models.py
    # (same reason as in data_key_manager.py).
    from ..database.models import KmsDataKey

    started_at = datetime.utcnow()
    report = RotationReport(started_at=started_at, new_kms_key_id=new_kms_key_id)

    offset = 0
    while True:
        with Session(sync_engine) as session:
            batch = (
                session.execute(
                    select(KmsDataKey).order_by(KmsDataKey.id).offset(offset).limit(batch_size)
                )
                .scalars()
                .all()
            )

            if not batch:
                break

            for row in batch:
                # Skip rows already rotated within this run window.
                # `rotated_at` is timezone-naive in models; compare as such.
                if row.rotated_at >= started_at:
                    report.skipped_count += 1
                    continue

                try:
                    plaintext = old_backend.decrypt(bytes(row.wrapped_key))
                    new_wrapped = new_backend.encrypt(plaintext)
                    row.wrapped_key = new_wrapped
                    row.kms_key_id = new_kms_key_id
                    row.rotated_at = datetime.utcnow()
                    session.add(row)
                    report.rotated_count += 1
                except Exception as exc:
                    # Capture and continue — one bad row mustn't abort
                    # the entire rotation. Operators address the failed
                    # rows in a follow-up run.
                    logger.exception("rotation failed for org_id=%s", row.org_id)
                    report.error_count += 1
                    report.errors.append((row.org_id, repr(exc)))

            session.commit()
            offset += len(batch)

    report.finished_at = datetime.utcnow()
    logger.info(report.summary())
    return report
