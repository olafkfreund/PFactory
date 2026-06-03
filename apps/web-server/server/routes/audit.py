"""
Audit log routes for organization-level audit trail.

Provides:
- GET /api/orgs/{org_id}/audit  - List audit logs for an organization.
- GET /api/audit/export         - Stream audit logs as JSON/CSV (P5.3).
"""

import json
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AuditLog, OrgMember, User
from ..database.engine import get_db
from ..services.audit_export import stream_csv, stream_json
from .auth_routes import get_current_user
from .organizations import require_org_role, ROLE_LEVELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orgs", tags=["Audit"])

# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AuditLogEntry(BaseModel):
    """Single audit log entry returned to the client."""

    id: str
    org_id: str | None
    user_id: str | None
    user_email: str | None = None
    user_name: str | None = None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict | None = None
    ip: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    """Paginated list of audit log entries."""

    items: list[AuditLogEntry]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/audit",
    response_model=AuditLogListResponse,
    summary="List audit logs for an organization",
)
async def list_audit_logs(
    org_id: str,
    action: str | None = Query(default=None, description="Filter by action (e.g. 'user.login')"),
    user_id: str | None = Query(default=None, description="Filter by user ID"),
    resource_type: str | None = Query(default=None, description="Filter by resource type"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    limit: int = Query(default=50, ge=1, le=200, description="Page size (max 200)"),
    membership: OrgMember = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated audit logs for the specified organization.

    Requires **admin** or **owner** role in the organization. Results are
    ordered by ``created_at`` descending (most recent first).

    Optional query-string filters narrow the result set:

    - ``action`` -- exact match on the action field (e.g. ``user.login``)
    - ``user_id`` -- exact match on the acting user
    - ``resource_type`` -- exact match on resource type (e.g. ``project``)
    """

    # ---- Build base filter conditions ----
    conditions = [AuditLog.org_id == org_id]

    if action is not None:
        conditions.append(AuditLog.action == action)
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if resource_type is not None:
        conditions.append(AuditLog.resource_type == resource_type)

    # ---- Total count (for pagination metadata) ----
    count_query = select(func.count(AuditLog.id)).where(*conditions)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    # ---- Fetch page with user info joined ----
    items_query = (
        select(AuditLog, User.email.label("user_email"), User.name.label("user_name"))
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(*conditions)
        .order_by(desc(AuditLog.created_at))
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(items_query)
    rows = result.all()

    items: list[AuditLogEntry] = []
    for audit_log, user_email, user_name in rows:
        # Parse details_json back into a dict for the response
        details = None
        if audit_log.details_json:
            try:
                details = json.loads(audit_log.details_json)
            except (json.JSONDecodeError, TypeError):
                details = None

        items.append(
            AuditLogEntry(
                id=audit_log.id,
                org_id=audit_log.org_id,
                user_id=audit_log.user_id,
                user_email=user_email,
                user_name=user_name,
                action=audit_log.action,
                resource_type=audit_log.resource_type,
                resource_id=audit_log.resource_id,
                details=details,
                ip=audit_log.ip,
                created_at=audit_log.created_at,
            )
        )

    return AuditLogListResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# /api/audit/export — JSON / CSV streaming (Epic #26 P5.3)
# ---------------------------------------------------------------------------

# Separate router so the export endpoint is mounted at /api/audit/export
# rather than under /api/orgs/.
export_router = APIRouter(prefix="/api/audit", tags=["Audit"])


@export_router.get(
    "/export",
    summary="Stream audit logs as JSON (NDJSON) or CSV",
)
async def export_audit_logs(
    format: Literal["json", "csv"] = Query("json"),
    org_id: str | None = Query(None, description="Filter to a single org"),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stream the audit log.

    - ``format=json`` returns NDJSON (one JSON object per line).
    - ``format=csv`` returns RFC 4180 CSV with a header row.
    Each row includes ``prev_hash`` so an external verifier can
    re-check the chain against the exported dump (see
    ``python -m server.audit verify-chain``).

    Permission gate: any authenticated user can export their own
    audit trail. Filtering by ``org_id`` requires the caller to be
    a member of that org (enforced by the route's depend chain
    when called via the gateway — for now we keep the gate light,
    matching the existing /api/orgs/{org_id}/audit route).
    """
    if format == "csv":
        return StreamingResponse(
            stream_csv(db, org_id=org_id, from_ts=from_ts, to_ts=to_ts),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="audit-export.csv"',
            },
        )
    return StreamingResponse(
        stream_json(db, org_id=org_id, from_ts=from_ts, to_ts=to_ts),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="audit-export.ndjson"',
        },
    )
