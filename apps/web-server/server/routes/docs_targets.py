"""
Docs target connections routes — Backstage / Confluence sinks for plan→docs (P4b).

The plan→docs emit (design §6e) writes a plan as Markdown/TechDocs to the repo by
default and, when configured, to Backstage and/or Confluence. This module stores
those connections per user (the API token encrypted at rest) and exposes a
``/test`` reachability probe so the Settings UI can validate before saving.

Endpoints:
- GET    /api/docs-targets           — list current user's connections
- POST   /api/docs-targets           — create a connection
- GET    /api/docs-targets/{id}      — fetch one connection
- PUT    /api/docs-targets/{id}      — update a connection
- DELETE /api/docs-targets/{id}      — delete a connection
- POST   /api/docs-targets/test      — test arbitrary credentials (no save)
- POST   /api/docs-targets/{id}/test — test stored credentials
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import DocsTargetConnection, User
from ..database.engine import get_db
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/docs-targets", tags=["Docs Targets"])

_TOKEN_TAIL_LEN = 4

DocsKind = Literal["backstage", "confluence"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DocsTargetCreate(BaseModel):
    kind: DocsKind
    label: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl
    api_token: SecretStr | None = Field(default=None, max_length=2048)
    space: str | None = Field(default=None, max_length=255)
    enabled_by_default: bool = False


class DocsTargetUpdate(BaseModel):
    kind: DocsKind | None = None
    label: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: HttpUrl | None = None
    api_token: SecretStr | None = Field(default=None, max_length=2048)
    space: str | None = Field(default=None, max_length=255)
    enabled_by_default: bool | None = None


class DocsTargetTestRequest(BaseModel):
    """Test arbitrary credentials before saving."""

    kind: DocsKind
    base_url: HttpUrl
    api_token: SecretStr | None = None
    space: str | None = None


class DocsTargetResponse(BaseModel):
    id: str
    kind: str
    label: str
    base_url: str
    api_token_preview: str | None
    space: str | None
    enabled_by_default: bool
    created_at: str
    updated_at: str
    last_used_at: str | None


class DocsTargetTestResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    detail: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_token(token: str | None) -> str | None:
    if not token:
        return None
    if len(token) <= _TOKEN_TAIL_LEN:
        return "*" * len(token)
    return "*" * (len(token) - _TOKEN_TAIL_LEN) + token[-_TOKEN_TAIL_LEN:]


def _to_response(conn: DocsTargetConnection) -> DocsTargetResponse:
    return DocsTargetResponse(
        id=conn.id,
        kind=conn.kind,
        label=conn.label,
        base_url=conn.base_url,
        api_token_preview=_mask_token(conn.api_token),
        space=conn.space,
        enabled_by_default=conn.enabled_by_default,
        created_at=conn.created_at.isoformat(),
        updated_at=conn.updated_at.isoformat(),
        last_used_at=conn.last_used_at.isoformat() if conn.last_used_at else None,
    )


def _probe(
    kind: str,
    base_url: str,
    api_token: str | None,
    space: str | None,
    timeout: int = 10,
) -> DocsTargetTestResponse:
    """Reachability probe for the configured docs sink.

    Backstage: ``GET {base_url}/api/catalog/entities?limit=1``.
    Confluence: ``GET {base_url}/rest/api/space/{space}`` (or /space) with a
    Bearer token.  All network errors are caught and returned as structured
    feedback rather than raised.
    """
    base = base_url.rstrip("/")
    headers: dict[str, str] = {"Accept": "application/json"}
    if kind == "backstage":
        url = f"{base}/api/catalog/entities?limit=1"
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
    elif kind == "confluence":
        url = f"{base}/rest/api/space/{space}" if space else f"{base}/rest/api/space"
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
    else:  # pragma: no cover - guarded by the Literal type
        return DocsTargetTestResponse(ok=False, error=f"Unknown kind: {kind}")

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            return DocsTargetTestResponse(
                ok=200 <= code < 300,
                status_code=code,
                detail=f"Reachable ({kind})",
            )
    except urllib.error.HTTPError as exc:
        # 401/403 means we reached the server but the token is wrong/missing.
        return DocsTargetTestResponse(
            ok=False,
            status_code=exc.code,
            error=f"HTTP {exc.code} {exc.reason}",
        )
    except urllib.error.URLError as exc:
        return DocsTargetTestResponse(ok=False, error=f"Connection failed: {exc.reason}")
    except Exception as exc:  # pragma: no cover - defensive
        return DocsTargetTestResponse(ok=False, error=f"Unexpected error: {exc}")


async def _get_owned(
    target_id: str, user: User, db: AsyncSession
) -> DocsTargetConnection:
    result = await db.execute(
        select(DocsTargetConnection).where(
            DocsTargetConnection.id == target_id,
            DocsTargetConnection.user_id == user.id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Docs target not found"
        )
    return conn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DocsTargetResponse])
async def list_targets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DocsTargetResponse]:
    result = await db.execute(
        select(DocsTargetConnection)
        .where(DocsTargetConnection.user_id == user.id)
        .order_by(DocsTargetConnection.created_at.desc())
    )
    return [_to_response(c) for c in result.scalars().all()]


@router.post("", response_model=DocsTargetResponse, status_code=status.HTTP_201_CREATED)
async def create_target(
    body: DocsTargetCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocsTargetResponse:
    conn = DocsTargetConnection(
        user_id=user.id,
        kind=body.kind,
        label=body.label,
        base_url=str(body.base_url),
        api_token=body.api_token.get_secret_value() if body.api_token else None,
        space=body.space or None,
        enabled_by_default=body.enabled_by_default,
    )
    db.add(conn)
    try:
        await db.commit()
    except Exception as exc:  # likely UniqueConstraint violation
        await db.rollback()
        logger.warning("Failed to create docs target: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A docs target with that label already exists",
        ) from exc
    await db.refresh(conn)
    return _to_response(conn)


@router.get("/{target_id}", response_model=DocsTargetResponse)
async def get_target(
    target_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocsTargetResponse:
    return _to_response(await _get_owned(target_id, user, db))


@router.put("/{target_id}", response_model=DocsTargetResponse)
async def update_target(
    target_id: str,
    body: DocsTargetUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocsTargetResponse:
    conn = await _get_owned(target_id, user, db)

    if body.kind is not None:
        conn.kind = body.kind
    if body.label is not None:
        conn.label = body.label
    if body.base_url is not None:
        conn.base_url = str(body.base_url)
    if body.api_token is not None:
        # Empty string clears the token; non-empty replaces it
        conn.api_token = body.api_token.get_secret_value() or None
    if body.space is not None:
        conn.space = body.space or None
    if body.enabled_by_default is not None:
        conn.enabled_by_default = body.enabled_by_default

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning("Failed to update docs target: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Update conflict (duplicate label?)",
        ) from exc
    await db.refresh(conn)
    return _to_response(conn)


@router.delete("/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    conn = await _get_owned(target_id, user, db)
    await db.delete(conn)
    await db.commit()


@router.post("/test", response_model=DocsTargetTestResponse)
async def test_arbitrary(
    body: DocsTargetTestRequest,
    user: User = Depends(get_current_user),
) -> DocsTargetTestResponse:
    """Test arbitrary credentials before saving (the Settings 'Test' button)."""
    import asyncio

    return await asyncio.to_thread(
        _probe,
        body.kind,
        str(body.base_url),
        body.api_token.get_secret_value() if body.api_token else None,
        body.space,
    )


@router.post("/{target_id}/test", response_model=DocsTargetTestResponse)
async def test_stored(
    target_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocsTargetTestResponse:
    """Test the credentials of a stored connection."""
    import asyncio

    conn = await _get_owned(target_id, user, db)
    return await asyncio.to_thread(
        _probe, conn.kind, conn.base_url, conn.api_token, conn.space
    )
