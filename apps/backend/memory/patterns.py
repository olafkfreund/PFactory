#!/usr/bin/env python3
"""
Patterns and Gotchas Management
================================

Functions for managing code patterns and gotchas (pitfalls to avoid).
"""

import logging
from pathlib import Path

from . import retention, trust
from .graphiti_helpers import get_graphiti_memory, is_graphiti_memory_enabled, run_async
from .locking import locked
from .paths import get_memory_dir

logger = logging.getLogger(__name__)

_sent: dict[tuple[Path, str], int] = {}  # Graphiti writes per memory dir, per process

GOTCHAS_HEADER = "# Gotchas and Pitfalls\n\nThings to watch out for in this codebase:\n\n"
PATTERNS_HEADER = "# Code Patterns\n\nEstablished patterns to follow in this codebase:\n\n"


def _bullets(path: Path) -> list[str]:
    if not path.exists():
        return []
    stripped = (raw.strip() for raw in path.read_text().split("\n"))
    return [s[2:].strip() for s in stripped if s.startswith("- ")]


def _append_bullet(spec_dir: Path, kind: str, text: str) -> None:
    """Locked, deduplicated bullet append; the Graphiti copy is gated (#3797)."""
    memory_dir = get_memory_dir(spec_dir)
    path = memory_dir / f"{kind}s.md"
    text = text.strip()
    tainted = kind == "gotcha" and trust.tainted()
    inline = " " + trust.UNVERIFIED.strip()  # one line, so a merge keeps it
    try:
        with locked(memory_dir):
            if not text or text in {b.removesuffix(inline) for b in _bullets(path)}:
                return
            with path.open("a") as f:
                if not path.stat().st_size:
                    f.write(GOTCHAS_HEADER if kind == "gotcha" else PATTERNS_HEADER)
                f.write(f"- {text}{inline if tainted else ''}\n")
    except OSError:
        logger.warning("%s not recorded in %s", kind, memory_dir, exc_info=True)
        return

    if not is_graphiti_memory_enabled():
        return
    sent = _sent.get((memory_dir, kind), 0)
    if sent >= retention.CAPS[retention.session_type()]:
        return  # the file path's per-session cap applies to Graphiti too
    _sent[(memory_dir, kind)] = sent + 1
    try:
        graphiti = get_graphiti_memory(spec_dir)
        if graphiti:
            save = graphiti.save_gotcha if kind == "gotcha" else graphiti.save_pattern
            run_async(save(text + (trust.UNVERIFIED if tainted else "")))
    except Exception as e:  # noqa: BLE001 - Graphiti is best-effort
        logger.warning(f"Graphiti {kind} save failed: {e}")


def append_gotcha(spec_dir: Path, gotcha: str) -> None:
    """
    Append a gotcha (pitfall to avoid) to the gotchas list.

    Deduplicated; written under the memory lock (skipped if it stays busy);
    marked unverified while the trust window is open.

    Example:
        append_gotcha(spec_dir, "Database connections must be closed in workers")
    """
    _append_bullet(spec_dir, "gotcha", gotcha)


def load_gotchas(spec_dir: Path) -> list[str]:
    """Load all gotchas."""
    return _bullets(get_memory_dir(spec_dir) / "gotchas.md")


def append_pattern(spec_dir: Path, pattern: str) -> None:
    """
    Append a code pattern to follow (deduplicated, written under the memory lock).

    Example:
        append_pattern(spec_dir, "Use try/except with specific exceptions")
    """
    _append_bullet(spec_dir, "pattern", pattern)


def load_patterns(spec_dir: Path) -> list[str]:
    """Load all code patterns."""
    return _bullets(get_memory_dir(spec_dir) / "patterns.md")
