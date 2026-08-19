"""Seal/unseal a secret string that lives inside a JSON store on disk (#537).

The DB side of Epic #26 already encrypts credential *columns* through
``crypto.EncryptedString``. The JSON stores under ``PROJECTS_DATA_DIR``
(``claude-profiles.json``, ``settings.json``) never got the same treatment, so
a Claude OAuth token sat there in plaintext with nothing but ``chmod 0600``
in front of it. This module is the JSON-store equivalent: it reuses the exact
same ``crypto.kms.get_backend()`` key material, so there is no second key to
manage, rotate, or provision.

Wire format
-----------
``enc.v1:<urlsafe-base64 of backend.encrypt(utf-8 plaintext)>``

The prefix is what makes the migration safe: ``unseal`` can tell a sealed
value from a legacy plaintext one without guessing.

Migration: read-both / write-new
--------------------------------
``unseal`` accepts an unprefixed (legacy plaintext) value and returns it
unchanged, so a store written before this change keeps working with no
migration step and no start-up conversion. Every write goes through ``seal``,
so the first time a profile is created, renamed, activated, deleted or
refreshed by the OAuth poll — all of which rewrite the whole file — the legacy
values are silently upgraded to ciphertext.

DEPRECATION: the plaintext passthrough in ``unseal`` is scheduled for removal
in **PFactory 1.5.0**. By then every live store has been rewritten many times
over; after removal an unprefixed value is treated as absent (empty), which
degrades to the next credential source rather than shipping a bad token to the
Claude CLI.

What this does and does not protect
-----------------------------------
Protects: the *file* — a PVC snapshot, a volume backup, a stray ``cat`` of the
data dir, a support bundle, or the store being copied out of the pod no longer
yields a usable token.

Does NOT protect: anything that can read the process environment. The key
arrives as ``KMS_FERNET_KEY`` (or a cloud KMS backend's credentials) in the pod
env, so ``kubectl exec`` into the pod, a root shell on the node, or code
execution inside the app can still decrypt. If no key is configured at all, the
value is written in plaintext exactly as before and a warning says so — that is
deliberately honest rather than deriving a key from a file sitting next to the
ciphertext, which would buy nothing over the ``0600`` already there.
"""

from __future__ import annotations

import base64
import copy
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from .kms import encryption_is_required, get_backend

_log = logging.getLogger(__name__)

# A parsed JSON store payload, and the per-value transform applied to its
# credential fields (seal on write, unseal on read).
JsonStore = dict[str, Any]
_Mapper = Callable[[str | None], str | None]

SEALED_PREFIX = "enc.v1:"

# Profile fields that hold a credential. "token" is the legacy field name
# normalize_profile_fields() migrates away from; both are handled so a
# half-migrated store cannot leak. "apiKey" covers api-profiles.json, which has
# the same {"profiles": [...]} shape and holds provider API keys.
PROFILE_SECRET_KEYS = ("oauthToken", "token", "apiKey")

# Top-level AppSettings fields (settings.json) that hold a credential.
APP_SETTINGS_SECRET_KEYS = (
    "globalClaudeOAuthToken",
    "globalOpenAIApiKey",
    "globalAnthropicApiKey",
    "emailMicrosoftClientSecret",
    "emailGoogleClientSecret",
)


@lru_cache(maxsize=1)
def _warn_no_key() -> None:
    """Warn exactly once per process that nothing is encrypting the stores."""
    _log.warning(
        "No KMS key configured (KMS_FERNET_KEY / APP_KMS_BACKEND); secrets in "
        "the JSON stores stay in plaintext behind chmod 0600. Set a key to "
        "encrypt them at rest."
    )


def is_sealed(value: str) -> bool:
    """True when ``value`` carries the sealed-wire-format prefix."""
    return isinstance(value, str) and value.startswith(SEALED_PREFIX)


def seal(value: str | None) -> str | None:
    """Encrypt ``value`` for storage in a JSON file. Idempotent.

    Falls back to returning the plaintext (with a one-time warning) when NO KMS
    backend was selected — a deployment without a key must still be able to save
    a profile, and it is no worse off than before this change.

    When a backend WAS selected (AIFactory#1290) the fallback is a lie: the
    operator believes these credentials are encrypted. There we raise, so the
    save fails loudly and retryably instead of quietly writing a token in the
    clear.
    """
    if not value or is_sealed(value):
        return value
    try:
        blob = get_backend().encrypt(value.encode("utf-8"))
    except Exception:
        # Broad on purpose: each KMS backend raises its own errors (RuntimeError
        # for a missing key, botocore/hvac/azure/google client errors for the
        # cloud ones). Saving a profile must not 500 because no key was ever
        # configured -- but it MUST 500 when a configured KMS is unusable.
        if encryption_is_required():
            raise
        _warn_no_key()
        return value
    return SEALED_PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")


def unseal(value: str | None) -> str | None:
    """Decrypt ``value``, or return it unchanged when it is legacy plaintext.

    Returns ``""`` when a sealed value cannot be decrypted (wrong/rotated key,
    tampering). Callers treat that as "no credential" and fall through their
    resolution chain, which is safer than handing ciphertext to a CLI.
    """
    if not value or not is_sealed(value):
        # ponytail: legacy plaintext passthrough — remove in 1.5.0, see module docstring.
        return value
    try:
        blob = base64.urlsafe_b64decode(value[len(SEALED_PREFIX) :].encode("ascii"))
        return get_backend().decrypt(blob).decode("utf-8")
    except Exception:  # noqa: BLE001 - see seal(); plus InvalidTag / binascii
        _log.error(
            "Failed to decrypt a sealed secret from a JSON store - wrong or "
            "rotated KMS key? Treating it as absent."
        )
        return ""


def _map_profiles(data: Any, fn: _Mapper) -> Any:
    """Apply ``fn`` to every secret field of every profile in ``data``.

    Returns a deep copy — callers routinely keep using the dict they passed to
    ``save_profiles`` (e.g. to sync CLAUDE_CODE_OAUTH_TOKEN from the active
    profile), and mutating it in place would hand them ciphertext.
    """
    if not isinstance(data, dict):
        return data
    data = copy.deepcopy(data)
    for profile in data.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        for key in PROFILE_SECRET_KEYS:
            if profile.get(key):
                profile[key] = fn(profile[key])
    return data


def seal_profiles(data: Any) -> Any:
    """Seal every credential in a {"profiles": [...]} payload (write path).

    Accepts any payload and returns non-dicts untouched, so the write helpers
    can call it with no ``isinstance`` guard of their own -- a guard there would
    be a second path to the file with no sealing on it.
    """
    return _map_profiles(data, seal)


def unseal_profiles(data: Any) -> Any:
    """Unseal every credential in a {"profiles": [...]} payload (read path)."""
    return _map_profiles(data, unseal)


def _map_fields(data: JsonStore, keys: tuple[str, ...], fn: _Mapper) -> JsonStore:
    """Apply ``fn`` to each of ``keys`` present (and truthy) in flat ``data``."""
    data = dict(data)
    for key in keys:
        if data.get(key):
            data[key] = fn(data[key])
    return data


def seal_fields(data: JsonStore, keys: tuple[str, ...] = APP_SETTINGS_SECRET_KEYS) -> JsonStore:
    """Seal the named top-level fields of a flat JSON payload (write path)."""
    return _map_fields(data, keys, seal)


def unseal_fields(data: JsonStore, keys: tuple[str, ...] = APP_SETTINGS_SECRET_KEYS) -> JsonStore:
    """Unseal the named top-level fields of a flat JSON payload (read path)."""
    return _map_fields(data, keys, unseal)
