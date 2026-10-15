"""tools: the tools advertised to the model match the handlers that run them.

``definitions._ALL_TOOLS`` and ``ToolExecutor._handlers`` are separate tables.
An advertised tool without a handler fails at call time; a handler that is not
advertised is dead code the model can never reach.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

from factory_invariants import registry


def _tools_have_handlers() -> Iterator[str]:
    from tools import definitions
    from tools.executor import ToolExecutor

    advertised = set(definitions._ALL_TOOLS)
    handled = set(ToolExecutor(Path(tempfile.gettempdir()))._handlers)
    for name in sorted(advertised - handled):
        yield f"tool {name!r} is advertised to the model but has no handler"
    for name in sorted(handled - advertised):
        yield f"handler {name!r} exists but the tool is never advertised"


registry.register("tools.executor", _tools_have_handlers)
