"""Sign a Task Contract so AIFactory will trust it (epic #65, child 8).

This mirrors AIFactory's ``trusted_plan`` HMAC envelope byte-for-byte, so a
contract signed here verifies on the AIFactory side
(``POST /api/tasks/from-plan``) and unlocks the skip-planning fast-path. The
canonical payload is the contract minus its ``approval`` block, joined with the
approval metadata — identical to AIFactory's ``_signing_bytes``.

Key: ``AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY`` (authority = ``pfactory``). Never
log or embed the key.

Key ids (#401)
--------------
A signed envelope MAY carry an optional key id (``kid``), bound into the signed
bytes exactly as AIFactory's ``trusted_plan._signing_bytes`` does. Without one,
AIFactory's ``AIFACTORY_TRUSTED_PLAN_RETIRED_KIDS`` has nothing to name and a
leaked key can only be answered by rotating the secret in place — which
invalidates every in-flight approved contract at once.

The signing key and its id are resolved together by :func:`key_from_env`, from
``AIFACTORY_TRUSTED_PLAN_KEY_<AUTHORITY>__<KID>``. The legacy unkeyed
``AIFACTORY_TRUSTED_PLAN_KEY_<AUTHORITY>`` still works and still produces a
byte-identical no-``kid`` envelope, so nothing changes until a keyed var is
deployed.
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

# Separates authority from key id in ``AIFACTORY_TRUSTED_PLAN_KEY_<AUTH>__<KID>``.
# Must stay identical to AIFactory trusted_plan._KID_SEP.
_KID_SEP = "__"

# Picks the kid to sign with when several keyed vars are configured at once
# (the rotation overlap). Unnecessary while exactly one is set.
_KID_ENV = "PFACTORY_TRUSTED_PLAN_KID"


def _canonical(obj: Any) -> str:
    """Deterministic JSON — must match AIFactory trusted_plan._canonical."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plan_core(contract: dict) -> dict:
    """The contract minus its approval envelope (the bytes that get signed)."""
    return {k: v for k, v in contract.items() if k != APPROVAL_KEY}


def _signing_bytes(
    contract: dict,
    approved_by: str,
    approval_timestamp: str,
    contract_version: str,
    kid: str | None = None,
) -> bytes:
    """Canonical payload over the contract and its approval metadata.

    The key id, when present, is appended so a signature cannot be replayed
    under a different kid. An empty/``None`` kid appends nothing, keeping the
    bytes byte-identical to a legacy signature — this must mirror AIFactory
    ``trusted_plan._signing_bytes`` exactly.
    """
    parts = [
        _canonical(_plan_core(contract)),
        approved_by,
        approval_timestamp,
        contract_version,
    ]
    if kid:
        parts.append(kid)
    return "|".join(parts).encode("utf-8")


def key_from_env(
    authority: str = "pfactory", env: dict | None = None
) -> tuple[str | None, str | None]:
    """Resolve the signing key **and its key id** for ``authority``.

    Returns ``(key, kid)``. Key material and kid are chosen together on
    purpose: signing with one key while stamping another key's id produces an
    envelope AIFactory rejects, so they must never be read independently.

    * ``AIFACTORY_TRUSTED_PLAN_KEY_<AUTHORITY>__<KID>`` → ``(key, kid)``; the
      kid is lowercased, matching AIFactory's case-insensitive keyring lookup
      and its retired-kid tokens.
    * ``AIFACTORY_TRUSTED_PLAN_KEY_<AUTHORITY>`` (legacy, no kid) →
      ``(key, None)``; signs exactly as before.
    * neither → ``(None, None)``; the contract is emitted unsigned.

    Raises ``ValueError`` rather than guessing when the configuration is
    ambiguous (several keyed vars and no ``PFACTORY_TRUSTED_PLAN_KID``) or
    contradictory (``PFACTORY_TRUSTED_PLAN_KID`` names a kid with no key).
    Silently falling back to the legacy key there would emit exactly the
    unrevocable contract this exists to prevent.
    """
    env = env if env is not None else os.environ
    prefix = f"{_ENV_KEY_PREFIX}{authority.upper()}"
    keyed_prefix = prefix + _KID_SEP
    keyed = {
        name[len(keyed_prefix) :].strip().lower(): value
        for name, value in env.items()
        if name.upper().startswith(keyed_prefix) and value
    }
    keyed.pop("", None)
    pinned = (env.get(_KID_ENV) or "").strip().lower()

    if pinned:
        if pinned not in keyed:
            raise ValueError(f"{_KID_ENV}={pinned!r} but {keyed_prefix}{pinned.upper()} is not set")
        return keyed[pinned], pinned
    if len(keyed) > 1:
        raise ValueError(
            f"{len(keyed)} signing keys configured for authority {authority!r} "
            f"({', '.join(sorted(keyed))}) — set {_KID_ENV} to choose one"
        )
    if keyed:
        kid, key = next(iter(keyed.items()))
        return key, kid
    return env.get(prefix) or None, None


def sign_contract(  # noqa: PLR0913 - a signature needs all of its inputs; kid is optional
    contract: dict,
    *,
    key: str,
    approval_timestamp: str,
    approved_by: str = "pfactory",
    contract_version: str = CONTRACT_VERSION,
    kid: str | None = None,
) -> dict:
    """Produce the approval envelope for ``contract`` (does not mutate it).

    Pass ``kid`` to sign with a rotating key id: it is stamped into the
    envelope and bound into the signature, which is what lets AIFactory revoke
    this key alone. Omit it for the legacy single-key envelope.
    """
    signature = hmac.new(
        key.encode("utf-8"),
        _signing_bytes(contract, approved_by, approval_timestamp, contract_version, kid),
        hashlib.sha256,
    ).hexdigest()
    envelope = {
        "approved_by": approved_by,
        "approval_timestamp": approval_timestamp,
        "plan_contract_version": contract_version,
        "signature": signature,
    }
    if kid:
        envelope["kid"] = kid
    return envelope


def attach_signature(  # noqa: PLR0913 - mirrors sign_contract's inputs; kid is optional
    contract: dict,
    *,
    key: str,
    approval_timestamp: str,
    approved_by: str = "pfactory",
    contract_version: str = CONTRACT_VERSION,
    kid: str | None = None,
) -> dict:
    """Sign ``contract`` and embed the envelope under ``approval`` (in place)."""
    contract[APPROVAL_KEY] = sign_contract(
        contract,
        key=key,
        approval_timestamp=approval_timestamp,
        approved_by=approved_by,
        contract_version=contract_version,
        kid=kid,
    )
    return contract
