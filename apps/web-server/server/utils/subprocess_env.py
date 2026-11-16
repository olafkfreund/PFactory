"""
Subprocess environment helper.

PFactory is OAuth-only by design — the Claude Agent SDK should never see
``ANTHROPIC_API_KEY`` because that would silently bill against the user's
direct API account instead of their Claude Code subscription
(see ``apps/backend/core/auth.py`` for the canonical policy comment).

Every place PFactory spawns a subprocess that may run a Claude CLI / SDK
call must build its env via ``make_subprocess_env()`` instead of bare
``os.environ.copy()``. The user's interactive PTY shell is the deliberate
exception — that's their own shell, they expect their normal env.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from core.child_env import CLAUDE_KEEP, child_env  # noqa: E402

__all__ = ["CLAUDE_KEEP", "RUNNER_STRIP", "child_env", "make_subprocess_env"]

# Env vars we explicitly strip from subprocess environments to prevent
# silent direct-API billing. Keep this list narrow — anything not in here
# is passed through unchanged.
_STRIP_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_FILE",
)

# Control-plane secrets no runner reads (no runtime read in apps/backend, Factory#3737).
# Everything else, credentials a runner needs included, passes through unchanged.
RUNNER_STRIP: frozenset[str] = frozenset(
    {
        "JWT_SECRET",
        "APP_JWT_SECRET",
        "APP_DATABASE_URL",
        "AIFACTORY_TOKEN",
        "AIFACTORY_MARKER_NONCE",
        "AIFACTORY_TRUSTED_CONTRACT",
        "APP_CFACTORY_READ_KEY",
        "CFACTORY_READ_KEY",
        "NIX_ACCESS_TOKENS",
        "NIX_CONFIG",
    }
)


def make_subprocess_env(
    extra: Mapping[str, str] | None = None,
    *,
    strip_anthropic_api_key: bool = True,
) -> dict[str, str]:
    """Return a copy of ``os.environ`` safe to pass to ``subprocess.*``.

    By default removes the Anthropic direct-API credentials so subprocesses
    PFactory spawns can never silently bill the user's API account. The
    SDK auth path (``CLAUDE_CODE_OAUTH_TOKEN``) is preserved.

    Args:
        extra: Optional mapping of additional vars to set on top of the
            scrubbed env.
        strip_anthropic_api_key: Caller can pass ``False`` only when the
            spawned process explicitly NEEDS the direct API key — e.g.
            an opt-in batch invocation that the user has consented to
            via Settings. Default ``True`` matches PFactory's policy.

    Returns:
        A plain dict suitable for ``env=`` on ``subprocess.*`` calls.
    """
    env = {k: v for k, v in os.environ.items() if k.upper() not in RUNNER_STRIP}
    if strip_anthropic_api_key:
        for var in _STRIP_VARS:
            env.pop(var, None)
    if extra:
        env.update(extra)
    return env
