"""Subscription-only auth policy for the agentic provider layer.

PFactory's plan-processing agents should run on the operator's *subscription*
(Claude Code OAuth, Codex/ChatGPT login, Antigravity/Gemini CLI login) — not on
metered API keys that bill silently. ``core/auth.py`` already refuses
``ANTHROPIC_API_KEY`` for Claude; this module extends the same posture to the
subprocess providers (Codex, Gemini/Antigravity) that would otherwise pick up
``OPENAI_API_KEY`` / ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` from the environment
and quietly switch to API billing.

Default **ON** (subscription-only), matching the established Claude posture.
Operators who deliberately want metered API-key billing opt out with
``PFACTORY_ALLOW_API_KEYS=1``.

The scrub applies only to a *copy* of the env handed to a provider subprocess —
the global ``os.environ`` is untouched, so the Graphiti memory embedders that
legitimately read these keys keep working. Stdlib-only.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# API-key env vars that flip a subprocess provider from subscription login to
# metered API billing, keyed by provider family.
API_KEY_ENV_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "codex": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def api_keys_allowed() -> bool:
    """True when the operator has explicitly opted into metered API-key billing."""
    return _truthy(os.environ.get("PFACTORY_ALLOW_API_KEYS"))


def subscription_only() -> bool:
    """The default posture: agentic providers run on subscriptions, not API keys."""
    return not api_keys_allowed()


def scrub_api_keys(env: dict[str, str], provider: str) -> dict[str, str]:
    """Return a copy of ``env`` with ``provider``'s API-key vars removed when the
    subscription-only policy is in effect.

    A present-and-stripped key is logged once so the posture is honest, never
    silent. No-op when API keys are explicitly allowed or the provider has no
    known key vars.
    """
    if api_keys_allowed():
        return env
    scrubbed = dict(env)
    for key in API_KEY_ENV_BY_PROVIDER.get(provider, ()):
        if scrubbed.pop(key, None):
            logger.warning(
                "%s: ignoring %s — PFactory runs agentic providers on your "
                "subscription, not metered API keys (set PFACTORY_ALLOW_API_KEYS=1 "
                "to override).",
                provider,
                key,
            )
    return scrubbed
