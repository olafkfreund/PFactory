"""Hot-path hooks that feed the scratchpad (RFC-0021 Phase 3)."""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import scratchpad, trust

logger = logging.getLogger(__name__)
Hook = Callable[[dict[str, Any], str | None, Any], Awaitable[dict[str, Any]]]


def make_failure_hook(memory_dir: Path) -> Hook:
    """A PostToolUseFailure hook that counts normalised tool errors.

    Never raises and never changes the tool outcome; user interrupts are not
    errors and are ignored.
    """

    async def failure_hook(
        input_data: dict[str, Any],
        tool_use_id: str | None = None,
        context: Any | None = None,
    ) -> dict[str, Any]:
        del tool_use_id, context
        try:
            if not input_data.get("is_interrupt"):
                error = str(input_data.get("error") or "")
                if error.strip():
                    scratchpad.observe(
                        memory_dir,
                        "error_fingerprint",
                        f"{input_data.get('tool_name', '')}: {error}",
                        untrusted=trust.tainted(),
                    )
        except Exception:  # noqa: BLE001 - a hook must never break the tool call
            logger.debug("scratchpad failure hook skipped", exc_info=True)
        return {}

    return failure_hook


async def taint_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None = None,
    context: Any | None = None,
) -> dict[str, Any]:
    """PreToolUse hook for WebFetch/WebSearch: open the untrusted window."""
    del input_data, tool_use_id, context
    trust.taint()
    return {}
