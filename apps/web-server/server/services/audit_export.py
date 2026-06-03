"""Audit log export streaming (Epic #26 P5.3).

Streaming export reduces memory pressure on large tenants — even
50k-row exports fit in a single 5MB JSON payload at typical event
sizes, but we don't want the server materializing it all at once.

Two formats supported:
  - JSON: newline-delimited JSON objects (NDJSON). Each line is one
    audit row with prev_hash. External verifiers re-compute the
    chain by reading line-by-line.
  - CSV: standard RFC 4180 CSV with header row. Same column set as
    JSON (minus the nested `details` dict — JSON-encoded as a
    single CSV cell).

The route layer wraps these helpers in a FastAPI StreamingResponse.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import AuditLog
from .audit_chain import serialize_for_export

# Stable CSV column order. NEVER reorder — external tooling
# downstream depends on this.
CSV_COLUMNS = [
    "id",
    "created_at",
    "action",
    "user_id",
    "org_id",
    "resource_type",
    "resource_id",
    "ip",
    "details_json",
    "prev_hash",
    "retention_until",
]


def _row_for_csv(row: AuditLog) -> list[str]:
    """Flatten an AuditLog row into the CSV column order."""
    def _str(v):
        if v is None:
            return ""
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)
    return [
        _str(row.id),
        _str(row.created_at),
        _str(row.action),
        _str(row.user_id),
        _str(row.org_id),
        _str(row.resource_type),
        _str(row.resource_id),
        _str(row.ip),
        _str(row.details_json),
        _str(row.prev_hash),
        _str(row.retention_until),
    ]


async def stream_json(
    db: AsyncSession,
    *,
    org_id: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> AsyncIterator[bytes]:
    """Yield NDJSON lines for matching audit rows, ordered by created_at."""
    q = select(AuditLog).order_by(AuditLog.created_at.asc())
    if org_id is not None:
        q = q.where(AuditLog.org_id == org_id)
    if from_ts is not None:
        q = q.where(AuditLog.created_at >= from_ts)
    if to_ts is not None:
        q = q.where(AuditLog.created_at <= to_ts)
    result = await db.execute(q)
    for row in result.scalars():
        yield (json.dumps(serialize_for_export(row)) + "\n").encode("utf-8")


async def stream_csv(
    db: AsyncSession,
    *,
    org_id: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
) -> AsyncIterator[bytes]:
    """Yield CSV rows (header first) for matching audit rows."""
    # Header.
    buf = io.StringIO()
    csv.writer(buf).writerow(CSV_COLUMNS)
    yield buf.getvalue().encode("utf-8")

    q = select(AuditLog).order_by(AuditLog.created_at.asc())
    if org_id is not None:
        q = q.where(AuditLog.org_id == org_id)
    if from_ts is not None:
        q = q.where(AuditLog.created_at >= from_ts)
    if to_ts is not None:
        q = q.where(AuditLog.created_at <= to_ts)
    result = await db.execute(q)
    for row in result.scalars():
        buf = io.StringIO()
        csv.writer(buf).writerow(_row_for_csv(row))
        yield buf.getvalue().encode("utf-8")
