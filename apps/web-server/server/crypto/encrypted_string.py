"""SQLAlchemy TypeDecorator that transparently encrypts string columns.

Use as a column type in models — the encryption boundary is invisible
to call sites::

    class IntegrationToken(Base):
        value: Mapped[str] = mapped_column(EncryptedString(), nullable=False)

Plaintext goes in via standard ORM assignment; ciphertext lives in the
DB; plaintext comes back on read.

Implementation: stores ciphertext as ``LargeBinary``. Backend selection
happens once at first use via ``crypto.kms.get_backend()`` — see
``crypto.kms.__init__`` for the env-var precedence.
"""

from __future__ import annotations

from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

from .kms import get_backend


class EncryptedString(TypeDecorator):
    """String column transparently encrypted at rest.

    ``impl = LargeBinary`` — the on-disk shape is ``bytes``, so a
    ``pg_dump`` of the column produces ciphertext only; no plaintext
    leak.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Plaintext str → ciphertext bytes on the way in."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(
                f"EncryptedString expects str on write, got {type(value).__name__}"
            )
        return get_backend().encrypt(value.encode("utf-8"))

    def process_result_value(self, value, dialect):
        """Ciphertext bytes → plaintext str on the way out."""
        if value is None:
            return None
        return get_backend().decrypt(bytes(value)).decode("utf-8")
