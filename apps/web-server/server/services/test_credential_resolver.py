"""Resolve stored test-target credentials at hand-off (#107, task 4).

The backend agent runs in a venv **without the DB driver**, so ``store:<id>``
references in ``.pfactory.yml`` are resolved **here** — the web-server owns the
encrypted ``test_target_credentials`` table. ``EncryptedString`` transparently
decrypts the secret on read; this service returns the plaintext
``(username, secret)`` so the executor hand-off can inject them as ephemeral
env (resolved values are never persisted back).

Pairs with the backend's ``resolve_test_target_credentials`` (which handles
``env:``/``vault:`` refs); together they cover every ``test_credentials`` ref.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import TestTargetCredential

#: ``.pfactory.yml`` ref prefix for a portal-stored credential.
STORE_PREFIX = "store:"


class StoreCredentialNotFound(KeyError):
    """Raised when a ``store:<id>`` ref names no stored credential."""


def parse_store_ref(ref: str) -> str | None:
    """Return the credential id for a ``store:<id>`` ref, else ``None``."""
    if isinstance(ref, str) and ref.startswith(STORE_PREFIX):
        return ref[len(STORE_PREFIX) :]
    return None


async def resolve_store_credential(db: AsyncSession, cred_id: str) -> tuple[str | None, str]:
    """Decrypt a stored credential → ``(username, secret)``; bump ``last_used_at``.

    Raises :class:`StoreCredentialNotFound` when no credential has that id.
    The secret is decrypted by ``EncryptedString`` on attribute read.
    """
    result = await db.execute(
        select(TestTargetCredential).where(TestTargetCredential.id == cred_id)
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise StoreCredentialNotFound(cred_id)

    username, secret = cred.username, cred.secret
    cred.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    return username, secret
