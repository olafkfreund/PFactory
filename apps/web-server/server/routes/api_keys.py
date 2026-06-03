"""
API key management routes for programmatic access.

Provides:
- POST   /api/keys           - Generate a new API key
- GET    /api/keys           - List current user's API keys
- DELETE /api/keys/{key_id}  - Revoke (delete) an API key
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import ApiKey, OrgMember
from ..database.engine import get_db
from .auth_routes import get_current_user
from ..database import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/keys", tags=["API Keys"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_PREFIX = "acw_"
KEY_PREVIEW_LENGTH = 8  # Number of chars from the raw key to show as preview

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _store_key_hash(raw_key: str) -> str:
    """Build the stored value: ``prefix$sha256hex``.

    The first *KEY_PREVIEW_LENGTH* characters of the raw key are preserved
    so that the list endpoint can show a short preview without ever storing
    the full key in plaintext.
    """
    preview = raw_key[:KEY_PREVIEW_LENGTH]
    digest = _hash_key(raw_key)
    return f"{preview}${digest}"


def _extract_preview(stored_hash: str) -> str:
    """Extract the key preview from the stored hash value.

    Returns the prefix portion before the ``$`` separator.  Falls back to
    ``acw_****`` if the stored value uses an older format without a preview.
    """
    if "$" in stored_hash:
        return stored_hash.split("$", 1)[0]
    return "acw_****"


def _extract_digest(stored_hash: str) -> str:
    """Extract the raw SHA-256 digest from the stored hash value."""
    if "$" in stored_hash:
        return stored_hash.split("$", 1)[1]
    return stored_hash


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Human-readable name for the key")
    org_id: str = Field(..., description="Organization ID this key is scoped to")
    scopes: list[str] | None = Field(
        default=None,
        description="Optional list of permission scopes (e.g. ['read', 'write'])",
    )
    expires_in_days: int | None = Field(
        default=None,
        gt=0,
        le=365,
        description="Optional expiration in days from now (max 365)",
    )


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    org_id: str
    scopes: list[str] | None
    key_preview: str
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class CreateApiKeyResponse(ApiKeyResponse):
    """Returned only on key creation. Contains the raw key which cannot be
    retrieved again after this response."""

    raw_key: str = Field(
        ...,
        description="The full API key. Store it securely -- it cannot be retrieved again.",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CreateApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new API key",
)
async def create_api_key(
    body: CreateApiKeyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new API key for the current user within an organization.

    The raw key is returned **only** in this response and is never stored in
    plaintext.  Only a SHA-256 hash (plus a short preview prefix) is persisted.
    """

    # Verify user is a member of the specified organization
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == body.org_id,
            OrgMember.user_id == current_user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    # Generate a secure random key with the acw_ prefix
    raw_key = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"

    # Compute expiration if requested
    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    # Serialize scopes as comma-separated string for storage
    scopes_str: str | None = None
    if body.scopes is not None:
        scopes_str = ",".join(body.scopes)

    # Create the API key record
    api_key = ApiKey(
        user_id=current_user.id,
        org_id=body.org_id,
        key_hash=_store_key_hash(raw_key),
        name=body.name,
        scopes=scopes_str,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    logger.info(
        f"API key created: {api_key.name} (id={api_key.id}) "
        f"for user {current_user.id} in org {body.org_id}"
    )

    return CreateApiKeyResponse(
        id=api_key.id,
        name=api_key.name,
        org_id=api_key.org_id,
        scopes=body.scopes,
        key_preview=raw_key[:KEY_PREVIEW_LENGTH],
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        created_at=api_key.created_at,
        raw_key=raw_key,
    )


@router.get(
    "",
    response_model=list[ApiKeyResponse],
    summary="List current user's API keys",
)
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all API keys belonging to the current user.

    The response includes metadata and a short preview of each key but
    never the full key or its hash.
    """

    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()

    return [
        ApiKeyResponse(
            id=key.id,
            name=key.name,
            org_id=key.org_id,
            scopes=key.scopes.split(",") if key.scopes else None,
            key_preview=_extract_preview(key.key_hash),
            last_used_at=key.last_used_at,
            expires_at=key.expires_at,
            created_at=key.created_at,
        )
        for key in keys
    ]


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke (delete) an API key",
)
async def revoke_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an API key by deleting it.

    Only the owner of the key (the user who created it) can revoke it.
    """

    # Verify the key exists and belongs to the current user
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.user_id == current_user.id,
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or does not belong to you",
        )

    key_name = api_key.name
    await db.delete(api_key)
    await db.commit()

    logger.info(
        f"API key revoked: {key_name} (id={key_id}) by user {current_user.id}"
    )

    return None
