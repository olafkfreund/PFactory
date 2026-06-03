"""Git credential management routes (epic #82 PR-C).

Provides CRUD for stored credentials used by the portal-managed clone
flow (#82 PR-A) to fetch private repositories. V1 supports HTTPS
Personal Access Tokens (PATs) only — Deploy Keys + GitHub App install
IDs are tracked as follow-ups on epic #82.

Endpoints:
- POST   /api/git-credentials              — store a new credential
- GET    /api/git-credentials              — list credentials (org-scoped)
- DELETE /api/git-credentials/{cred_id}    — delete a credential

Tokens are encrypted at rest via ``EncryptedString`` (Epic #26 P2.3) —
the stored representation is opaque LargeBinary; only the clone
service can read the plaintext, and never logs it.

Access control: anyone who's a member of the credential's org can
list / use it; only the original creator (or org admins) can delete it.
For the v1.0 pilot this is fine — multi-tenant ACL hardening is part of
Epic #35 (Tenant Isolation).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import GitCredential, OrgMember, User
from ..database.engine import get_db
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/git-credentials", tags=["Git Credentials"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateGitCredentialRequest(BaseModel):
    org_id: str = Field(..., description="Organization that owns this credential")
    name: str = Field(
        ..., min_length=1, max_length=255, description="Human-readable label"
    )
    token: str = Field(
        ...,
        min_length=1,
        description=(
            "The Personal Access Token. Never logged, encrypted at rest, "
            "cannot be retrieved after creation."
        ),
    )
    kind: str = Field(
        default="pat",
        description="Credential kind. V1 supports 'pat' only.",
    )
    host: str | None = Field(
        default=None,
        description="Informational host label (e.g. 'github.com'). Not enforced.",
    )
    username: str | None = Field(
        default=None,
        description=(
            "Username for the HTTPS URL injection. Most providers accept "
            "'oauth2' or 'x-token-auth'. Defaults to 'oauth2' if omitted."
        ),
    )


class GitCredentialResponse(BaseModel):
    """Response shape — never carries the token plaintext."""

    id: str
    org_id: str
    name: str
    kind: str
    host: str | None
    username: str | None
    created_at: datetime
    last_used_at: datetime | None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _verify_org_membership(
    db: AsyncSession, user: User, org_id: str
) -> OrgMember:
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
    response_model=GitCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store a new Git credential",
)
async def create_git_credential(
    body: CreateGitCredentialRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_org_membership(db, current_user, body.org_id)

    if body.kind != "pat":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "V1 supports kind='pat' only. Deploy Keys + GitHub App "
                "credentials land in a later iteration."
            ),
        )

    cred = GitCredential(
        org_id=body.org_id,
        name=body.name,
        kind=body.kind,
        host=body.host,
        username=body.username or "oauth2",
        token=body.token,
        created_by=current_user.id,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)

    logger.info(
        "git credential created: id=%s name=%s org=%s by=%s",
        cred.id,
        cred.name,
        cred.org_id,
        current_user.id,
    )
    return cred


@router.get(
    "",
    response_model=list[GitCredentialResponse],
    summary="List Git credentials in an organization",
)
async def list_git_credentials(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_org_membership(db, current_user, org_id)
    result = await db.execute(
        select(GitCredential)
        .where(GitCredential.org_id == org_id)
        .order_by(GitCredential.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete(
    "/{cred_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Git credential",
)
async def delete_git_credential(
    cred_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GitCredential).where(GitCredential.id == cred_id)
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found",
        )
    # Either the creator OR an org admin can delete. v1.0 keeps this
    # permissive; tighter ACL in Epic #35.
    await _verify_org_membership(db, current_user, cred.org_id)
    await db.execute(sql_delete(GitCredential).where(GitCredential.id == cred_id))
    await db.commit()
    logger.info(
        "git credential deleted: id=%s name=%s by=%s",
        cred.id,
        cred.name,
        current_user.id,
    )
