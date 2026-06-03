"""Local-key Fernet (AES-256-GCM) backend.

Used by:
  - Local dev where no external KMS is wired up.
  - The P2 acceptance test suite (`tests/secrets/`).

Production deployments use one of the cloud backends (aws_kms / azure_kv /
gcp_kms / vault_transit) added in P2.4 — this backend exists so the
EncryptedString TypeDecorator has a runnable target end-to-end before
the cloud plumbing lands.

Wire format: 12-byte random nonce | AES-256-GCM ciphertext+tag.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FernetBackend:
    """AES-256-GCM with a 32-byte root key from env.

    Despite the name (kept for backwards-compatible API symmetry with the
    `cryptography` library's `Fernet` class), we use raw AESGCM rather
    than `cryptography.fernet.Fernet` — AESGCM gives us native AES-256
    + 12-byte nonces (Fernet is AES-128-CBC + HMAC, weaker for our
    bank-grade target).
    """

    NONCE_BYTES = 12  # AESGCM standard
    KEY_BYTES = 32    # AES-256

    def __init__(self, root_key: bytes) -> None:
        if len(root_key) != self.KEY_BYTES:
            raise ValueError(
                f"FernetBackend requires a {self.KEY_BYTES}-byte key "
                f"(got {len(root_key)} bytes)"
            )
        self._aead = AESGCM(root_key)

    @classmethod
    def from_env(cls) -> "FernetBackend":
        """Construct from the ``KMS_FERNET_KEY`` env var (URL-safe base64)."""
        raw = os.environ.get("KMS_FERNET_KEY") or os.environ.get("APP_KMS_FERNET_KEY")
        if not raw:
            raise RuntimeError(
                "KMS_FERNET_KEY env var is not set. Generate one with: "
                "python -c 'import base64, secrets; "
                "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'"
            )
        try:
            key = base64.urlsafe_b64decode(raw.encode())
        except Exception as exc:
            raise RuntimeError(f"KMS_FERNET_KEY is not valid URL-safe base64: {exc}") from exc
        return cls(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(self.NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext, associated_data=None)
        return nonce + ciphertext

    def decrypt(self, blob: bytes) -> bytes:
        if len(blob) < self.NONCE_BYTES + 16:  # +16 for GCM tag
            raise ValueError(
                f"ciphertext too short: {len(blob)} bytes "
                f"(min {self.NONCE_BYTES + 16})"
            )
        nonce, ciphertext = blob[: self.NONCE_BYTES], blob[self.NONCE_BYTES :]
        return self._aead.decrypt(nonce, ciphertext, associated_data=None)
