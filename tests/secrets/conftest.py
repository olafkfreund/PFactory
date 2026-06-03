"""Pytest fixtures for P2 EncryptedString / KMS acceptance tests.

Tests are marked `@pytest.mark.secrets`. They use the **fernet** backend
by default (local symmetric key from env var) so they run anywhere
without external KMS dependencies. The per-backend tests in
`test_p2_kms_backends.py` opt into specific backends and skip when the
corresponding client library isn't installed.
"""

from __future__ import annotations

import os
import secrets

import pytest


@pytest.fixture
def fernet_key() -> str:
    """A random 32-byte URL-safe-base64 Fernet key, fresh per test."""
    import base64
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


@pytest.fixture(autouse=True)
def clean_kms_env(monkeypatch):
    """Strip KMS_* env between tests so leftover state doesn't leak."""
    for key in list(os.environ):
        if key.startswith("KMS_") or key in {"APP_KMS_BACKEND"}:
            monkeypatch.delenv(key, raising=False)
    yield
