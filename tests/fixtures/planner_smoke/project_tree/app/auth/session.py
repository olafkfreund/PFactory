"""Session module post-diff — the planner Glob/Greps against this tree."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SESSION_TTL_HOURS = 24
GRACE_WINDOW_MIN = 5


@dataclass
class Session:
    id: str
    user_id: str
    email: str
    created_at: str
    expires_at: str | None = None


_STORE: dict[str, Session] = {}


def _now_utc():
    return datetime.now(timezone.utc)


def get_session(session_id: str) -> Session | None:
    s = _STORE.get(session_id)
    if s is None:
        return None
    if s.expires_at is None:
        return s
    if datetime.fromisoformat(s.expires_at) <= _now_utc():
        del _STORE[session_id]
        return None
    return s


def refresh_session(session_id: str) -> Session | None:
    s = _STORE.get(session_id)
    if s is None or s.expires_at is None:
        return s
    expires = datetime.fromisoformat(s.expires_at)
    if expires - _now_utc() > timedelta(minutes=GRACE_WINDOW_MIN):
        return s
    s.expires_at = (_now_utc() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    return s


def logout_user(session_id: str) -> None:
    _STORE.pop(session_id, None)
