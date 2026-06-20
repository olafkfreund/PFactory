"""Presence-only existence probe for credential refs (RFC-0007 / #86 PR-a).

The curation gate needs to know whether a declared credential *exists* — without
ever handling or logging the secret value. ``probe_ref_exists`` returns:

    True   — the ref resolved (the credential is present)
    False  — the ref is confirmably absent (only determinable for ``env:``)
    None   — undeterminable here (e.g. a ``store:``/``vault:`` ref in a context
             with no backend configured) — deferred to the curation gate proper

It never returns the resolved value. ``env:`` is checked against ``os.environ``
(absence is knowable); every other scheme is probed by attempting a resolve and
reporting success/None only — a failure can mean "absent" OR "no backend", which
we must not conflate, so we honestly report None rather than a false "missing".
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def probe_ref_exists(ref: str | None, *, resolver=None) -> bool | None:
    """Presence-only existence check for a credential ref. Never returns the value.

    ``resolver(ref) -> value`` defaults to a best-effort ``CredentialBroker``.
    """
    if not ref or not isinstance(ref, str) or ":" not in ref:
        return None
    scheme, locator = ref.split(":", 1)
    if scheme == "env":
        return bool(os.environ.get(locator))  # absence is knowable for env:

    if resolver is None:
        try:
            from .broker import CredentialBroker

            resolver = CredentialBroker().resolve_ref
        except Exception:
            return None
    try:
        value = resolver(ref)
        return value is not None  # presence only; value is discarded, never logged
    except Exception:
        return None
