"""GDPR right-to-erasure service (Epic #26 P5.5).

Erasure deletes a user's PII without breaking the audit chain.
The key insight: replacing user_id with SHA-256(user_id) preserves
the hash-chain invariant (same input → same hash) while making the
identifier irreversible. Anyone holding the audit log can still
verify the chain, but can no longer link historical actions to a
specific natural person.

PII deleted:
  - users.email          → NULL
  - users.name           → NULL
  - users.avatar_url     → NULL
  - email_accounts rows for this user (cascades OAuth tokens)
  - audit_logs.user_id   → SHA-256(original user_id) (per row)
  - audit_logs.details_json — naive regex-based PII removal

PII preserved:
  - users.id (used as a foreign key elsewhere; the row stays but is
    tombstoned via users.gdpr_erased_at)
  - audit_logs rows themselves (legal-hold retention; only the user
    linkage is anonymized)

Limitations documented in guides/operations/audit-trail.md:
  - The redaction of details_json is best-effort. Operators should
    configure stricter redaction rules per their data-classification
    policy via env or the gdpr_redaction_rules module (v1.1).
  - If the original user_id is published anywhere outside the
    audit_logs table (e.g. external SIEM), erasure does NOT reach
    those external systems. Cross-system erasure is an operator-
    process concern.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import AuditLog, EmailAccount, User
from .audit_chain import GENESIS, compute_hash, row_as_mapping

logger = logging.getLogger(__name__)


def _hash_user_id(user_id: str) -> str:
    """Return a hex SHA-256 of the user id, padded to 36 chars to fit
    the existing user_id column width."""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:36]


def _redact_details_json(s: str | None, original_user_id: str, hashed: str) -> str | None:
    """Replace any literal occurrence of the original user_id with the
    hashed value inside the JSON blob. Also blanks `email` / `name`
    / `ip` fields if present."""
    if s is None:
        return None
    # Replace verbatim user_id (UUID format) — covers ``{"actor": "...uuid..."}``
    # and similar patterns regardless of position.
    s = s.replace(original_user_id, hashed)
    # Blank known-PII keys via a targeted regex. Conservative: only
    # touches values for these specific keys, never their structure.
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s
    # PII detection: a key is sensitive when its name contains any of
    # these tokens (case-insensitive). Conservative — false positives
    # are preferable to false negatives in a GDPR context.
    pii_substrings = ("email", "name", "ip", "phone", "address", "ssn")

    def _is_pii_key(k: str) -> bool:
        kl = k.lower()
        return any(s in kl for s in pii_substrings)

    def _walk(o):
        if isinstance(o, dict):
            return {
                k: ("<redacted>" if _is_pii_key(k) and v else _walk(v))
                for k, v in o.items()
            }
        if isinstance(o, list):
            return [_walk(x) for x in o]
        return o
    return json.dumps(_walk(obj))


async def erase_user(db: AsyncSession, user_id: str) -> dict:
    """Perform the full GDPR erasure for ``user_id``. Idempotent:
    re-running on an already-erased user is a no-op.

    Returns a summary dict (for the route to emit + log):
      {
        "user_id": "<original>",
        "hashed_user_id": "<sha256[:36]>",
        "audit_rows_anonymized": N,
        "email_accounts_deleted": M,
        "erased_at": "...",
      }
    """
    # 1. Look up the user (idempotency check).
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError(f"user_id {user_id!r} not found")
    if user.gdpr_erased_at is not None:
        return {
            "user_id": user.id,
            "hashed_user_id": _hash_user_id(user_id),
            "audit_rows_anonymized": 0,
            "email_accounts_deleted": 0,
            "erased_at": user.gdpr_erased_at.isoformat(),
            "idempotent": True,
        }

    hashed = _hash_user_id(user_id)

    # 2. Anonymize audit_logs. Update both user_id and details_json.
    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.user_id == user_id)
    )
    audit_rows = list(audit_result.scalars())
    for row in audit_rows:
        row.user_id = hashed
        row.details_json = _redact_details_json(
            row.details_json, original_user_id=user_id, hashed=hashed
        )

    # 3. Delete email accounts (OAuth tokens). Hard delete — the user
    # is asking to be forgotten; we have no legal basis to retain
    # them.
    ea_result = await db.execute(
        select(EmailAccount).where(EmailAccount.user_id == user_id)
    )
    ea_rows = list(ea_result.scalars())
    for ea in ea_rows:
        await db.delete(ea)

    # 4. Re-chain the audit log so verify_chain still passes
    # post-erasure. Walk all rows in created_at order; for each row,
    # set its prev_hash = compute_hash(prev_row's prev_hash, prev_row's
    # content). Includes rows we just modified — their post-erasure
    # content is now the canonical content. This is O(total rows) so
    # erasure on a 1M-row audit log walks the whole table once.
    # Acceptable for a one-time operator action; v1.1 will optimize
    # by re-chaining from the first modified row only.
    all_rows_result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.asc())
    )
    prev_hash_for_next = GENESIS
    for row in all_rows_result.scalars():
        row.prev_hash = prev_hash_for_next
        prev_hash_for_next = compute_hash(
            prev_hash_for_next, row_as_mapping(row)
        )

    # 5. Tombstone the user row.
    user.email = None
    user.name = None
    user.avatar_url = None
    user.gdpr_erased_at = datetime.utcnow()

    await db.commit()
    logger.info(
        "GDPR erasure complete for user_id=%s — %d audit rows anonymized, "
        "%d email accounts deleted",
        user_id, len(audit_rows), len(ea_rows),
    )

    return {
        "user_id": user_id,
        "hashed_user_id": hashed,
        "audit_rows_anonymized": len(audit_rows),
        "email_accounts_deleted": len(ea_rows),
        "erased_at": user.gdpr_erased_at.isoformat(),
        "idempotent": False,
    }
