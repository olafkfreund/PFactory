"""The per-service env rules as data."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Policy:
    """One service's env rules as data."""

    extra_env: str  # env var naming the operator's extra allowed names
    deny_exact: frozenset[str]  # service names on top of BASE_DENY_EXACT
    deny_pattern: re.Pattern[str]
    allow_pattern: re.Pattern[str]
    keep: frozenset[str] = frozenset()  # agent auth vars: never blanked
    keep_is_allowed: bool = False  # ``keep`` names pass is_agent_env_allowed
    credential_blocks_allow: bool = False  # *_KEY/*_TOKEN never match by pattern
    strip_vars: frozenset[str] = frozenset()  # upper-case names child_env drops
    blank_exempt: Callable[[str], bool] | None = field(default=None, compare=False)
