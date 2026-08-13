"""
In-app notification service with real-time WebSocket delivery.

Provides an in-memory notification store per user with automatic pruning,
WebSocket push delivery, and org-wide broadcast support.

Usage::

    from ..services.notification_service import notification_service

    # Send to a specific user
    await notification_service.notify(
        user_id="user-uuid",
        type="task_assigned",
        title="New task assigned",
        message="You have been assigned to task #007",
        data={"task_id": "007"},
    )

    # Send to all members of an organization
    await notification_service.notify_org(
        org_id="org-uuid",
        type="build_failed",
        title="Build failed",
        message="Task #007 build failed at QA validation",
        data={"task_id": "007", "phase": "qa"},
    )
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from factory_common.logsafe import sanitize_log

from ..database import OrgMember
from ..database.engine import async_session_factory
from ..websockets.events import send_to_user

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification data model (in-memory)
# ---------------------------------------------------------------------------


@dataclass
class Notification:
    """A single in-app notification."""

    id: str
    user_id: str
    type: str  # "task_assigned", "qa_complete", "merge_ready", "build_failed", "member_invited"
    title: str
    message: str
    data: dict
    read: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Serialize the notification for API responses and WebSocket payloads."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "data": self.data,
            "read": self.read,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Notification service
# ---------------------------------------------------------------------------


class NotificationService:
    """In-memory notification store with WebSocket delivery.

    Notifications are stored per user in a bounded list.  When the list
    exceeds ``MAX_NOTIFICATIONS_PER_USER``, the oldest entries are pruned
    automatically.

    Real-time delivery is handled by pushing each new notification over
    the global events WebSocket via ``send_to_user``.
    """

    MAX_NOTIFICATIONS_PER_USER: int = 100

    def __init__(self) -> None:
        # In-memory store keyed by user_id -> list of Notification (newest first)
        self._store: dict[str, list[Notification]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def notify(
        self,
        user_id: str,
        type: str,
        title: str,
        message: str,
        data: dict | None = None,
    ) -> Notification:
        """Create a notification and deliver it via WebSocket.

        Parameters
        ----------
        user_id:
            Target user ID.
        type:
            Notification type identifier (e.g. ``"task_assigned"``).
        title:
            Short human-readable title.
        message:
            Longer descriptive message body.
        data:
            Arbitrary JSON-serializable payload (task_id, org_id, etc.).

        Returns
        -------
        Notification
            The newly created notification object.
        """
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type=type,
            title=title,
            message=message,
            data=data or {},
        )

        self._append(user_id, notification)

        # Push over WebSocket (best-effort, never raises)
        try:
            await send_to_user(
                user_id,
                "notification:new",
                notification.to_dict(),
            )
        except Exception:
            logger.warning(
                "Failed to push notification via WebSocket: user_id=%s type=%s",
                sanitize_log(user_id),
                sanitize_log(type),
                exc_info=True,
            )

        # Attempt to send email notification (best-effort, never raises)
        try:
            await self._maybe_send_email(user_id, type, title, message, data or {})
        except Exception:
            logger.warning(
                "Failed to send email notification: user_id=%s type=%s",
                sanitize_log(user_id),
                sanitize_log(type),
                exc_info=True,
            )

        logger.debug(
            "Notification created: user_id=%s type=%s title=%s",
            sanitize_log(user_id),
            sanitize_log(type),
            sanitize_log(title),
        )
        return notification

    async def notify_org(
        self,
        org_id: str,
        type: str,
        title: str,
        message: str,
        data: dict | None = None,
    ) -> list[Notification]:
        """Send a notification to every member of an organization.

        Looks up org members from the database and calls :meth:`notify`
        for each one.

        Parameters
        ----------
        org_id:
            The organization whose members should be notified.
        type:
            Notification type identifier.
        title:
            Short human-readable title.
        message:
            Longer descriptive message body.
        data:
            Arbitrary JSON-serializable payload.  ``org_id`` is
            automatically injected.

        Returns
        -------
        list[Notification]
            The list of created notifications (one per member).
        """
        merged_data = {"org_id": org_id, **(data or {})}
        member_user_ids: list[str] = []

        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(OrgMember.user_id).where(OrgMember.org_id == org_id)
                )
                member_user_ids = [row[0] for row in result.all()]
        except Exception:
            logger.warning(
                "Failed to look up org members for notification: org_id=%s",
                sanitize_log(org_id),
                exc_info=True,
            )
            return []

        notifications: list[Notification] = []
        for uid in member_user_ids:
            n = await self.notify(
                user_id=uid,
                type=type,
                title=title,
                message=message,
                data=merged_data,
            )
            notifications.append(n)

        logger.debug(
            "Org notification sent: org_id=%s type=%s recipients=%d",
            sanitize_log(org_id),
            sanitize_log(type),
            len(notifications),
        )
        return notifications

    def get_unread_count(self, user_id: str) -> int:
        """Return the number of unread notifications for a user."""
        notifications = self._store.get(user_id, [])
        return sum(1 for n in notifications if not n.read)

    def get_notifications(
        self,
        user_id: str,
        limit: int = 50,
        include_read: bool = False,
    ) -> list[Notification]:
        """Return notifications for a user.

        Parameters
        ----------
        user_id:
            The user whose notifications to retrieve.
        limit:
            Maximum number of notifications to return.
        include_read:
            If ``False`` (default), only unread notifications are
            returned.  If ``True``, both read and unread are included.

        Returns
        -------
        list[Notification]
            Notifications ordered newest-first, up to ``limit``.
        """
        notifications = self._store.get(user_id, [])
        if not include_read:
            notifications = [n for n in notifications if not n.read]
        return notifications[:limit]

    def mark_read(self, user_id: str, notification_id: str) -> bool:
        """Mark a single notification as read.

        Returns ``True`` if the notification was found and updated,
        ``False`` otherwise.
        """
        for n in self._store.get(user_id, []):
            if n.id == notification_id:
                n.read = True
                return True
        return False

    def mark_all_read(self, user_id: str) -> int:
        """Mark all notifications for a user as read.

        Returns the number of notifications that were marked as read.
        """
        count = 0
        for n in self._store.get(user_id, []):
            if not n.read:
                n.read = True
                count += 1
        return count

    # ------------------------------------------------------------------
    # Email notification support
    # ------------------------------------------------------------------

    # Map notification types to NotificationSettings field names
    _TYPE_TO_SETTING = {
        "task_complete": "onTaskComplete",
        "task_completed": "onTaskComplete",
        "task_failed": "onTaskFailed",
        "build_failed": "onTaskFailed",
        "review_needed": "onReviewNeeded",
        "qa_complete": "onReviewNeeded",
    }

    async def _maybe_send_email(
        self,
        user_id: str,
        notification_type: str,
        title: str,
        message: str,
        data: dict,
    ) -> None:
        """Send an email notification if the user has email enabled and the event type is toggled on."""
        # Load app settings to check notification preferences
        try:
            from ..config import get_settings

            settings_cfg = get_settings()
            settings_file = Path(settings_cfg.PROJECTS_DATA_DIR) / "settings.json"
            if not settings_file.exists():
                return

            app_settings = json.loads(settings_file.read_text())
            notifications = app_settings.get("notifications", {})

            # Check master email toggle
            if not notifications.get("emailEnabled", False):
                return

            # Check specific event type toggle
            setting_key = self._TYPE_TO_SETTING.get(notification_type)
            if setting_key and not notifications.get(setting_key, True):
                return
        except Exception:
            logger.debug("Could not load notification settings for email check", exc_info=True)
            return

        # Send the email
        from .email_service import email_service

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        task_id = data.get("task_id", "")
        project_id = data.get("project_id", "")

        body_html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #0ea5e9;">{_html_escape(title)}</h2>
            <p>{_html_escape(message)}</p>
            <hr style="border: none; border-top: 1px solid #204660; margin: 16px 0;" />
            <p style="color: #6b7280; font-size: 13px;">
                Task: {_html_escape(task_id)}<br/>
                Project: {_html_escape(project_id)}<br/>
                Time: {now_str}
            </p>
            <p style="color: #9ca3af; font-size: 12px;">Sent by PFactory</p>
        </div>
        """

        await email_service.send_notification_email(
            user_id=user_id,
            subject=f"PFactory - {title}",
            body_html=body_html,
            body_text=f"{title}\n\n{message}\n\nTask: {task_id}\nProject: {project_id}\nTime: {now_str}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append(self, user_id: str, notification: Notification) -> None:
        """Append a notification to the user's store with auto-pruning."""
        if user_id not in self._store:
            self._store[user_id] = []

        # Insert at the beginning (newest first)
        self._store[user_id].insert(0, notification)

        # Prune oldest if over the limit
        if len(self._store[user_id]) > self.MAX_NOTIFICATIONS_PER_USER:
            self._store[user_id] = self._store[user_id][: self.MAX_NOTIFICATIONS_PER_USER]


def _html_escape(s: str) -> str:
    """Minimal HTML escaping for notification email content."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

notification_service = NotificationService()
