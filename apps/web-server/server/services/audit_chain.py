"""Hash chain for the audit log (Epic #26 P5.2 / P5.4).

The chain is a per-row SHA-256 of the previous row's canonical
content. The first row in the chain has ``prev_hash = GENESIS``.

Threat model:
  PROTECTS against: insertion / deletion / mutation of audit log
    rows by an attacker who has write access to the DB but cannot
    re-compute the chain (e.g., a compromised DB read-replica
    replayed forward).
  DOES NOT PROTECT against: an attacker who can ALSO re-compute the
    entire chain from any point forward (which any DB admin can do).
    Defense for that scenario = signed external anchor (e.g.,
    timestamping the daily chain head to an external authority).
    That's a v1.1 follow-up; documented in
    guides/operations/audit-trail.md.

Canonical encoding (the bytes we SHA-256):
  GENESIS for first row, else previous row's hash || \\x1f ||
  current row's content as ``id|action|user_id|org_id|created_at_iso|details_json``.
  The separator is ASCII Unit Separator (0x1f) so it can't appear in
  any reasonable field value.

GDPR erasure (P5.5): replaces user_id with SHA-256(user_id) and
NULLs out PII inside details_json BEFORE the chain hash is computed.
After erasure, the chain re-verifies because the same canonical
encoding produces the same hash (we never store the plaintext
user_id anywhere except the now-NULL user_id column itself).
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping

GENESIS = "GENESIS"
_SEP = b"\x1f"


def _canonical(row: Mapping) -> bytes:
    """Stable bytes representation of a row's auditable content.

    The canonical encoding is order-stable and includes every field
    that's protected by the chain. Adding a field here = chain
    re-verification breaks for older rows; treat as a forward-only
    schema change requiring a migration.
    """
    return _SEP.join(
        [
            (row["id"] or "").encode("utf-8"),
            (row["action"] or "").encode("utf-8"),
            (row.get("user_id") or "").encode("utf-8"),
            (row.get("org_id") or "").encode("utf-8"),
            (row.get("resource_type") or "").encode("utf-8"),
            (row.get("resource_id") or "").encode("utf-8"),
            _iso(row.get("created_at")).encode("utf-8"),
            (row.get("details_json") or "").encode("utf-8"),
        ]
    )


def _iso(value) -> str:
    """Render a datetime / str / None as a stable ISO-8601 string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.isoformat()


def compute_hash(prev_hash: str | None, row: Mapping) -> str:
    """Return the SHA-256 hex of this row's content chained to prev_hash.

    ``prev_hash`` of ``None`` is treated as the genesis sentinel.
    """
    prev = (prev_hash or GENESIS).encode("utf-8")
    digest = hashlib.sha256(prev + _SEP + _canonical(row)).hexdigest()
    return digest


def verify_chain(rows: Iterable[Mapping]) -> tuple[bool, int | None, str | None]:
    """Verify that every row's prev_hash matches the chained hash.

    Returns ``(ok, first_bad_index, reason)``:
      - ok=True, first_bad_index=None, reason=None when the chain
        verifies end-to-end.
      - ok=False with the 0-based index of the first row that fails
        + a human-readable reason.

    Rows must be ordered by ascending ``created_at`` (or any total
    order — the chain is order-sensitive).
    """
    prev_hash: str | None = None
    rows_list = list(rows)
    for i, row in enumerate(rows_list):
        expected_prev = GENESIS if i == 0 else prev_hash
        stored_prev = row.get("prev_hash") or GENESIS
        if stored_prev != expected_prev:
            return (
                False,
                i,
                f"row[{i}].prev_hash={stored_prev!r} != expected {expected_prev!r}",
            )
        # Compute THIS row's hash for the next iteration's prev.
        prev_hash = compute_hash(stored_prev, row)
    return True, None, None


def row_as_mapping(audit_row) -> dict:
    """Convert an AuditLog ORM instance to the dict shape compute_hash expects."""
    return {
        "id": audit_row.id,
        "action": audit_row.action,
        "user_id": audit_row.user_id,
        "org_id": audit_row.org_id,
        "resource_type": audit_row.resource_type,
        "resource_id": audit_row.resource_id,
        "created_at": audit_row.created_at,
        "details_json": audit_row.details_json,
        "prev_hash": audit_row.prev_hash,
    }


# CLI-friendly export helper. Used by the export endpoint AND the
# external verify script (so the same canonical encoding flows
# through both paths — the verifier can be run against an exported
# JSON dump in an air-gapped environment).
def serialize_for_export(audit_row) -> dict:
    """Stable JSON-serializable shape for /api/audit/export?format=json."""
    d = row_as_mapping(audit_row)
    d["created_at"] = _iso(d["created_at"])
    if d.get("details_json"):
        try:
            d["details"] = json.loads(d["details_json"])
        except (json.JSONDecodeError, TypeError):
            d["details"] = None
    return d
