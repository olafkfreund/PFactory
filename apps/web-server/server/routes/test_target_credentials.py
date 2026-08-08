"""Test-target credential management routes (#107).

Store credentials used to authenticate to a system-under-test, so generated
tests can log in without secrets pasted into test files. Mirrors the
git-credentials pattern: org-scoped, encrypted at rest, the secret is NEVER
returned via the API after creation.

Endpoints:
- POST   /api/test-credentials              — store a new credential
- GET    /api/test-credentials?org_id=...   — list credentials (metadata only)
- DELETE /api/test-credentials/{cred_id}    — delete a credential
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import OrgMember, TestTargetCredential, User
from ..database.engine import get_db
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test-credentials", tags=["Test Credentials"])

# Enum-by-convention; validated here, not in the DB, for forward-compat.
_VALID_KINDS = {"form", "api_token", "basic_auth", "totp"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateTestCredentialRequest(BaseModel):
    org_id: str = Field(..., description="Organization that owns this credential")
    name: str = Field(
        ..., min_length=1, max_length=255, description="Label referenced from .pfactory.yml"
    )
    kind: str = Field(default="form", description="form | api_token | basic_auth | totp")
    username: str | None = Field(
        default=None, description="Plaintext username/identifier (not a secret on its own)."
    )
    secret: SecretStr = Field(
        ...,
        min_length=1,
        description="The secret material (password / API token / TOTP seed). Never logged, encrypted at rest, cannot be retrieved after creation.",
    )
    extra: dict | None = Field(
        default=None,
        description="Optional kind-specific fields, e.g. {'otp_period': 30}. Encrypted.",
    )


class TestCredentialResponse(BaseModel):
    """Response shape — never carries ``secret`` or ``extra`` plaintext."""

    id: str
    org_id: str
    name: str
    kind: str
    username: str | None
    created_at: datetime
    last_used_at: datetime | None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _verify_org_membership(db: AsyncSession, user: User, org_id: str) -> OrgMember:
    """Raise 403 unless the user belongs to the org."""
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == org_id,
            OrgMember.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )
    return membership


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=TestCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store a new test-target credential",
)
async def create_test_credential(
    body: CreateTestCredentialRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_org_membership(db, current_user, body.org_id)

    if body.kind not in _VALID_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"kind must be one of {sorted(_VALID_KINDS)}",
        )

    cred = TestTargetCredential(
        org_id=body.org_id,
        name=body.name,
        kind=body.kind,
        username=body.username,
        secret=body.secret.get_secret_value(),
        extra=json.dumps(body.extra) if body.extra is not None else None,
        created_by=current_user.id,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)

    logger.info(
        "test credential created: id=%s name=%s org=%s by=%s",
        cred.id,
        cred.name,
        cred.org_id,
        current_user.id,
    )
    return cred


@router.get(
    "",
    response_model=list[TestCredentialResponse],
    summary="List test-target credentials in an organization",
)
async def list_test_credentials(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_org_membership(db, current_user, org_id)
    result = await db.execute(
        select(TestTargetCredential)
        .where(TestTargetCredential.org_id == org_id)
        .order_by(TestTargetCredential.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete(
    "/{cred_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a test-target credential",
)
async def delete_test_credential(
    cred_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(TestTargetCredential).where(TestTargetCredential.id == cred_id)
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    # Creator or any org member can delete (permissive in v1, matches
    # git-credentials; tighter ACL is a follow-up).
    await _verify_org_membership(db, current_user, cred.org_id)
    await db.execute(sql_delete(TestTargetCredential).where(TestTargetCredential.id == cred_id))
    await db.commit()
    logger.info(
        "test credential deleted: id=%s name=%s by=%s",
        cred.id,
        cred.name,
        current_user.id,
    )
