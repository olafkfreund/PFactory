"""KMS backend abstraction for PFactory's at-rest encryption.

The active backend is selected by the ``APP_KMS_BACKEND`` env var
(or just ``KMS_BACKEND`` outside the pydantic-settings prefix). Each
backend implements the minimal ``encrypt(bytes) -> bytes`` /
``decrypt(bytes) -> bytes`` protocol — the data layer (EncryptedString)
neither knows nor cares about the underlying key-management technology.

P2.1 ships only the ``fernet`` backend (local-key, for dev + tests).
P2.4 adds aws_kms, azure_kv, gcp_kms, vault_transit.
"""

from __future__ import annotations

import os
from typing import Protocol


class Backend(Protocol):
    """Minimum protocol every KMS backend implements."""

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt + authenticate. Result is an opaque blob — backends are
        free to prepend nonces/headers; the data layer treats it as bytes."""
        ...

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Reverse encrypt(). Raises ``InvalidTag`` (or backend-specific
        equivalent) when the blob has been tampered with."""
        ...


_INSTANCE: Backend | None = None


def get_backend() -> Backend:
    """Resolve the configured backend. Cached per process.

    Env var precedence (first match wins):
      - ``APP_KMS_BACKEND`` (per the pydantic-settings prefix convention)
      - ``KMS_BACKEND`` (the unprefixed form, used by tests)

    Default: ``fernet``.
    """
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    name = (
        os.environ.get("APP_KMS_BACKEND")
        or os.environ.get("KMS_BACKEND")
        or "fernet"
    ).strip().lower()

    if name == "fernet":
        from .fernet import FernetBackend
        _INSTANCE = FernetBackend.from_env()
        return _INSTANCE

    if name == "aws_kms":
        from .aws import AwsKmsBackend
        _INSTANCE = AwsKmsBackend.from_env()
        return _INSTANCE

    if name == "vault_transit":
        from .vault import VaultTransitBackend
        _INSTANCE = VaultTransitBackend.from_env()
        return _INSTANCE

    if name == "azure_kv":
        from .azure import AzureKeyVaultBackend
        _INSTANCE = AzureKeyVaultBackend.from_env()
        return _INSTANCE

    if name == "gcp_kms":
        from .gcp import GcpKmsBackend
        _INSTANCE = GcpKmsBackend.from_env()
        return _INSTANCE

    raise ValueError(
        f"unknown KMS backend {name!r} — supported: "
        "fernet, aws_kms, vault_transit, azure_kv, gcp_kms"
    )


def reset_backend_cache() -> None:
    """Test hook: force the next ``get_backend()`` call to re-read env."""
    global _INSTANCE
    _INSTANCE = None
