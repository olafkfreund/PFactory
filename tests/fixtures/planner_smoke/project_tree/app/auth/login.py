"""Login module post-diff."""

import uuid
from datetime import datetime, timedelta, timezone

from .session import _STORE, Session


def login_user(email: str, password: str) -> Session | None:
    user = _lookup(email, password)  # stub — actual user lookup omitted
    if user is None:
        return None
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=24)
    s = Session(
        id=session_id, user_id=user.id, email=email,
        created_at=now.isoformat(),
        expires_at=expires.isoformat(),
    )
    _STORE[session_id] = s
    return s


def _lookup(email, password):
    return None  # stub
