"""
Notification routes for in-app notification management.

Provides:
- GET  /api/notifications              - List current user's notifications
- GET  /api/notifications/unread-count - Get unread notification count
- POST /api/notifications/{id}/read    - Mark a single notification as read
- POST /api/notifications/read-all     - Mark all notifications as read
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..database import User
from ..services.notification_service import notification_service
from .auth_routes import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class NotificationResponse(BaseModel):
    """A single notification returned to the client."""

    id: str
    user_id: str
    type: str
    title: str
    message: str
    data: dict
    read: bool
    created_at: str


class NotificationListResponse(BaseModel):
    """List of notifications with metadata."""

    items: list[NotificationResponse]
    unread_count: int


class UnreadCountResponse(BaseModel):
    """Unread notification count."""

    count: int


class MarkReadResponse(BaseModel):
    """Response for mark-read operations."""

    success: bool


class MarkAllReadResponse(BaseModel):
    """Response for mark-all-read operations."""

    success: bool
    count: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List notifications for the current user",
)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100, description="Max notifications to return"),
    include_read: bool = Query(default=False, description="Include read notifications"),
    current_user: User = Depends(get_current_user),
):
    """Return the current user's notifications.

    By default only unread notifications are returned.  Set
    ``include_read=true`` to include already-read notifications.

    Results are ordered newest-first, up to ``limit``.
    """
    notifications = notification_service.get_notifications(
        user_id=current_user.id,
        limit=limit,
        include_read=include_read,
    )
    unread_count = notification_service.get_unread_count(current_user.id)

    return NotificationListResponse(
        items=[
            NotificationResponse(**n.to_dict())
            for n in notifications
        ],
        unread_count=unread_count,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get unread notification count",
)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
):
    """Return the number of unread notifications for the current user.

    This is a lightweight endpoint suitable for polling or badge updates.
    """
    count = notification_service.get_unread_count(current_user.id)
    return UnreadCountResponse(count=count)


@router.post(
    "/{notification_id}/read",
    response_model=MarkReadResponse,
    summary="Mark a notification as read",
)
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
):
    """Mark a single notification as read.

    Returns 404 if the notification does not exist or does not belong
    to the current user.
    """
    found = notification_service.mark_read(
        user_id=current_user.id,
        notification_id=notification_id,
    )
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    return MarkReadResponse(success=True)


@router.post(
    "/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark all notifications as read",
)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
):
    """Mark all of the current user's notifications as read.

    Returns the number of notifications that were marked as read.
    """
    count = notification_service.mark_all_read(current_user.id)
    return MarkAllReadResponse(success=True, count=count)
