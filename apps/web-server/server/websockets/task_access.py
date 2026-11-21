"""Per-task WebSocket authentication and project-org authorization."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, WebSocket

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


async def _project_denial_code(user: dict[str, Any], project_id: str | None) -> int | None:
    try:
        from server.database.engine import async_session_factory  # noqa: PLC0415

        async with async_session_factory() as db:
            await authorize_project_for_user(user, project_id, db)
        return None
    except HTTPException as exc:
        return 4403 if exc.status_code == 403 else 4404  # noqa: PLR2004
    except Exception:  # noqa: BLE001 — failed lookup denies the socket
        return 4404


async def _authorize_project(
    user: dict[str, Any],
    project_id: str | None,
    *,
    strict: bool,  # noqa: ARG001
) -> bool:
    return await _project_denial_code(user, project_id) is None


class TaskSocketAccess:
    """Authenticate and authorize a per-task WebSocket connection."""

    def __init__(self, websocket: WebSocket, task_id: str) -> None:
        self.websocket = websocket
        self.task_id = task_id
        self.project_id = _task_project_id(task_id)
        self.user: dict[str, Any] | None = None
        self._key_token: str | None = None
        self._last_recheck: float = 0.0

    async def _authorize(
        self, user: dict[str, Any], *, strict: bool, project_id: str | None = None
    ) -> bool:
        return await _authorize_project(
            user, self.project_id if project_id is None else project_id, strict=strict
        )

    async def connect(self) -> bool:
        """Authenticate and authorize before ``websocket.accept()``."""
        try:
            user = await authenticate_websocket(self.websocket)
        except WebSocketAuthError:
            return False

        if user is None or is_service_principal(user):
            return True

        if not self.project_id:
            await self.websocket.close(code=4404)
            return False

        denial_code = await _project_denial_code(user, self.project_id)
        if denial_code is not None:
            await self.websocket.close(code=denial_code)
            return False

        self.user = user
        token = _ws_extract_token(self.websocket)
        if user.get("api_key_id") and token and token.startswith("acw_"):
            self._key_token = token
            self._last_recheck = time.monotonic()

        return True

    async def recheck(self) -> bool:
        """Re-validate an ``acw_`` personal key when the recheck interval elapses."""
        if not self._key_token:
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


class AllTaskSocketAccess:
    """Authenticate an all-task socket and filter its visible task ids."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.user: dict[str, Any] | None = None
        self.key_token: str | None = None
        self.last_check: float = time.monotonic()

    async def connect(self) -> bool:
        try:
            self.user = await authenticate_websocket(self.websocket)
        except WebSocketAuthError:
            return False
        except Exception:  # noqa: BLE001 — unexpected auth failure denies the socket
            await self.websocket.close(code=4003)
            return False

        if self.user and self.user.get("api_key_id"):
            token = _ws_extract_token(self.websocket)
            if token and token.startswith("acw_"):
                self.key_token = token
                self.last_check = time.monotonic()
        return True

    async def recheck(self) -> bool:
        if not self.key_token or time.monotonic() - self.last_check < _KEY_RECHECK_SECONDS:
            return True
        try:
            principal = await _ws_acw_principal(self.key_token)
        except Exception:  # noqa: BLE001 — validator failure fails closed
            principal = None
        if principal is None:
            await self.websocket.close(code=4001)
            return False
        self.user = principal
        self.last_check = time.monotonic()
        return True

    async def readable(self, task_ids: list[str]) -> set[str]:
        if self.user is None or is_service_principal(self.user):
            return set(task_ids)
        strict = bool(self.user.get("api_key_id")) or _jwt_strict()
        projects: dict[str, bool] = {}
        allowed: set[str] = set()
        for task_id in task_ids:
            project_id = _task_project_id(task_id)
            if project_id is None:
                continue
            if project_id not in projects:
                projects[project_id] = await _authorize_project(
                    self.user, project_id, strict=strict
                )
            if projects[project_id]:
                allowed.add(task_id)
        return allowed
