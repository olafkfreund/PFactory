"""Sign a Task Contract so AIFactory will trust it (epic #65, child 8).

This mirrors AIFactory's ``trusted_plan`` HMAC envelope byte-for-byte, so a
contract signed here verifies on the AIFactory side
(``POST /api/tasks/from-plan``) and unlocks the skip-planning fast-path. The
canonical payload is the contract minus its ``approval`` block, joined with the
approval metadata — identical to AIFactory's ``_signing_bytes``.

Key: ``AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY`` (authority = ``pfactory``). Never
log or embed the key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

APPROVAL_KEY = "approval"
CONTRACT_VERSION = "2"
_ENV_KEY_PREFIX = "AIFACTORY_TRUSTED_PLAN_KEY_"


def _canonical(obj: Any) -> str:
    """Deterministic JSON — must match AIFactory trusted_plan._canonical."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plan_core(contract: dict) -> dict:
    """The contract minus its approval envelope (the bytes that get signed)."""
    return {k: v for k, v in contract.items() if k != APPROVAL_KEY}


def _signing_bytes(
    contract: dict, approved_by: str, approval_timestamp: str, contract_version: str
) -> bytes:
    payload = "|".join(
        (
            _canonical(_plan_core(contract)),
            approved_by,
            approval_timestamp,
            contract_version,
        )
    )
    return payload.encode("utf-8")


def key_from_env(authority: str = "pfactory", env: dict | None = None) -> str | None:
    """Read the signing key for ``authority`` (e.g. AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY)."""
    env = env if env is not None else os.environ
    return env.get(f"{_ENV_KEY_PREFIX}{authority.upper()}") or None


def sign_contract(
    contract: dict,
    *,
    key: str,
    approval_timestamp: str,
    approved_by: str = "pfactory",
    contract_version: str = CONTRACT_VERSION,
) -> dict:
    """Produce the approval envelope for ``contract`` (does not mutate it)."""
    signature = hmac.new(
        key.encode("utf-8"),
        _signing_bytes(contract, approved_by, approval_timestamp, contract_version),
        hashlib.sha256,
    ).hexdigest()
    return {
        "approved_by": approved_by,
        "approval_timestamp": approval_timestamp,
        "plan_contract_version": contract_version,
        "signature": signature,
    }


def attach_signature(
    contract: dict,
    *,
    key: str,
    approval_timestamp: str,
    approved_by: str = "pfactory",
    contract_version: str = CONTRACT_VERSION,
) -> dict:
    """Sign ``contract`` and embed the envelope under ``approval`` (in place)."""
    contract[APPROVAL_KEY] = sign_contract(
        contract,
        key=key,
        approval_timestamp=approval_timestamp,
        approved_by=approved_by,
        contract_version=contract_version,
    )
    return contract
