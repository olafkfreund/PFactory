"""Utilities shared across P2 EncryptedString / KMS acceptance tests."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SERVER_ROOT = REPO_ROOT / "apps" / "web-server"

if str(WEB_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_ROOT))


# Module names we evict between tests to force fresh engine/EncryptedString
# re-import under a new KMS_BACKEND / DATABASE_URL env. Same pattern as
# tests/postgres/test_p1_driver.py:_reimport_engine().
_MODULES_TO_EVICT = (
    "server.database.engine",
    "server.database",
    "server.crypto.encrypted_string",
    "server.crypto.kms",
    "server.crypto",
)


def reimport_crypto(env: dict[str, str]) -> None:
    """Re-import server.crypto with a fresh env (KMS_BACKEND, KMS_*_KEY)."""
    for k, v in env.items():
        os.environ[k] = v
    for m in _MODULES_TO_EVICT:
        sys.modules.pop(m, None)
    importlib.import_module("server.crypto")


def kms_backend_available(backend: str) -> bool:
    """True iff the named KMS backend's client lib is importable.

    Uses a wrapped `importlib.import_module` rather than `find_spec` —
    `find_spec` raises ModuleNotFoundError on nested paths (e.g.
    `azure.keyvault.keys`) when even the top-level `azure` namespace
    isn't installed, breaking the skipif-decorator evaluation.
    """
    import importlib
    lib_for = {
        "aws_kms": "boto3",
        "azure_kv": "azure.keyvault.keys",
        "gcp_kms": "google.cloud.kms",
        "vault_transit": "hvac",
        "fernet": "cryptography",
    }
    pkg = lib_for.get(backend)
    if not pkg:
        return False
    try:
        importlib.import_module(pkg)
        return True
    except ImportError:
        return False
