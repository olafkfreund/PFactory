"""Encrypted-at-rest secrets for PFactory (Epic #26 P2).

Public API:
    EncryptedString       SQLAlchemy TypeDecorator for credential columns.
    get_backend()         Factory returning the active KMS backend.
    DataKeyManager        Per-organization data-key cache (P2.2+).
"""

from .data_key_manager import DataKeyManager
from .encrypted_string import EncryptedString
from .kms import get_backend

__all__ = ["DataKeyManager", "EncryptedString", "get_backend"]
