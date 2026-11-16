"""Per-task WebSocket authentication and project-org authorization."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, WebSocket

from factory_common.logsafe import sanitize_log
from server.auth import (
    WebSocketAuthError,
    _ws_acw_principal,
    _ws_extract_token,
    authenticate_websocket,
)
from server.config import get_settings
from server.routes.project_authz import authorize_project_for_user, is_service_principal

logger = logging.getLogger(__name__)

_KEY_RECHECK_SECONDS = 300


def _jwt_strict() -> bool:
    return bool(get_settings().RBAC_ENFORCE_TASK_ROUTES)


def _task_project_id(task_id: str) -> str | None:
    project_id, sep, spec_id = task_id.partition(":")
    if not sep or not project_id or not spec_id:
        return None
    return project_id


class TaskSocketAccess:
    """Authenticate and authorize a per-task WebSocket connection."""

    def __init__(self, websocket: WebSocket, task_id: str) -> None:
        self.websocket = websocket
        self.task_id = task_id
        self.project_id = _task_project_id(task_id)
        self.user: dict[str, Any] | None = None
        self._key_token: str | None = None
        self._last_recheck: float = 0.0

    async def _authorize(self, user: dict[str, Any], *, strict: bool) -> bool:
        try:
            from server.database.engine import async_session_factory  # noqa: PLC0415

            async with async_session_factory() as db:
                await authorize_project_for_user(user, self.project_id, db)
            return True
        except HTTPException:
            if strict:
                return False
            logger.warning(
                "ws task access would deny project=%s user=%s",
                sanitize_log(self.project_id),
                sanitize_log(user.get("id")),
            )
            return True
        except Exception:  # noqa: BLE001 — strict principals fail closed on any lookup error
            if strict:
                return False
            logger.warning(
                "ws task access check failed project=%s user=%s",
                sanitize_log(self.project_id),
                sanitize_log(user.get("id")),
            )
            return True

    async def connect(self) -> bool:
        """Authenticate and authorize before ``websocket.accept()``."""
        try:
            user = await authenticate_websocket(self.websocket)
        except WebSocketAuthError:
            return False

        if user is None or is_service_principal(user):
            return True

        if not self.project_id:
            await self.websocket.close(code=4003)
            return False

        strict = bool(user.get("api_key_id")) or _jwt_strict()
        if not await self._authorize(user, strict=strict):
            await self.websocket.close(code=4003)
            return False

        self.user = user
        token = _ws_extract_token(self.websocket)
        if user.get("api_key_id") and token and token.startswith("acw_"):
            self._key_token = token
            self._last_recheck = time.monotonic()

        return True

    async def recheck(self) -> bool:
        """Re-validate an ``acw_`` personal key when the recheck interval elapses."""
        if not self._key_token or not (self.user or {}).get("api_key_id"):
            return True
        if time.monotonic() - self._last_recheck < _KEY_RECHECK_SECONDS:
            return True

        try:
            principal = await _ws_acw_principal(self._key_token)
        except Exception:  # noqa: BLE001 — validator failure fails closed
            principal = None

        if principal is None or not await self._authorize(principal, strict=True):
            await self.websocket.close(code=4001)
            return False

        self.user = principal
        self._last_recheck = time.monotonic()
        return True
