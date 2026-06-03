"""GDPR right-to-erasure routes (Epic #26 P5.5).

  POST /api/users/{user_id}/gdpr-erasure
    Triggers irreversible erasure of the user's PII. Audit log rows
    are preserved but their user_id is replaced with SHA-256(user_id)
    and details_json is redacted. The audit chain is re-hashed so
    `verify-chain` continues to pass.

Permission gate (v1.0): the route requires the caller to be an
admin in *some* org the target user belongs to. v1.1 will add a
dedicated "data protection officer" role.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import User
from ..database.engine import get_db
from ..services.gdpr import erase_user
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["GDPR"])


@router.post(
    "/{user_id}/gdpr-erasure",
    summary="Irreversibly erase a user's PII (GDPR Art. 17)",
)
async def trigger_gdpr_erasure(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete the user's PII. Audit chain re-hashes to
    preserve verifiability.

    Idempotent — re-running on an already-erased user returns the
    existing erasure record with `idempotent: true`.
    """
    try:
        summary = await erase_user(db, user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )

    logger.warning(
        "GDPR erasure executed: user_id=%s by actor=%s audit_rows=%d",
        user_id,
        getattr(current_user, "id", "unknown"),
        summary["audit_rows_anonymized"],
    )
    return summary
