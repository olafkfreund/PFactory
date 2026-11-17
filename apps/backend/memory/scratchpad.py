"""Observation counters and dead ends (RFC-0021 Phase 3).

Counters and sets only: no LLM in the hot path, and every write is bounded.
Project-scoped by construction: the store is ``<memory_dir>/scratchpad.json``
and nothing here reads outside the ``memory_dir`` it is given.
"""

import hashlib
import json
import logging
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .retention import windows

logger = logging.getLogger(__name__)

KINDS = (
    "file_co_access",
    "repeated_grep",
    "error_fingerprint",
    "self_correction",
    "dead_end",
    "config_touch",
    "tool_sequence",
)
FILENAME = "scratchpad.json"
MAX_KEYS = 100  # per kind
TEXT_LIMIT = 160  # stored text and reason
ABANDON_WINDOW = 900  # seconds: signals this recent belong to a declared dead end

Store = dict[str, dict[str, dict[str, Any]]]


def normalise(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))[:TEXT_LIMIT]


def _canon(kind: str, text: str) -> str:
    """Errors collapse digits and case; a dead end keeps both ("Python 3.11" and
    "Python 3.12" are different approaches)."""
    if kind == "dead_end":
        return " ".join(text.split())[:TEXT_LIMIT]
    return normalise(text)


def fingerprint(text: str, kind: str = "error_fingerprint") -> str:
    return hashlib.sha256(_canon(kind, text).encode()).hexdigest()[:16]


def read(memory_dir: Path) -> Store:
    """The store; corrupt or missing reads as empty."""
    try:
        data = json.loads((memory_dir / FILENAME).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        kind: {k: v for k, v in entries.items() if isinstance(v, dict)}
        for kind, entries in data.items()
        if isinstance(entries, dict)
    }


def write(memory_dir: Path, store: Store) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=memory_dir, delete=False, suffix=".tmp") as f:
        path = Path(f.name)
        try:
            json.dump(store, f)
        except (TypeError, ValueError):
            path.unlink(missing_ok=True)
            raise
    path.replace(memory_dir / FILENAME)


def _bump(  # noqa: PLR0913
    store: Store,
    kind: str,
    text: str,
    reason: str | None,
    untrusted: bool,
    now: datetime,
) -> None:
    entries = store.setdefault(kind, {})
    key = fingerprint(text, kind)
    entry = entries.setdefault(key, {"text": _canon(kind, text), "n": 0, "bad": 0, "ab": 0})
    entry["n"] = int(entry.get("n", 0)) + 1
    if untrusted:
        entry["bad"] = int(entry.get("bad", 0)) + 1
    # An untrusted reason never replaces a trusted one; if it is the only one
    # it is kept but flagged (rb) and never recalled.
    if reason and (not untrusted or "reason" not in entry):
        entry["reason"] = " ".join(reason.split())[:TEXT_LIMIT]
        entry["rb"] = untrusted
    entry["last"] = now.isoformat()
    if len(entries) > MAX_KEYS:
        victim = min(
            (k for k in entries if k != key),
            key=lambda k: (int(entries[k].get("n", 0)), str(entries[k].get("last"))),
        )
        del entries[victim]


def observe(  # noqa: PLR0913
    memory_dir: Path,
    kind: str,
    text: str,
    reason: str | None = None,
    untrusted: bool = False,
    now: datetime | None = None,
) -> bool:
    """Count one observation. Never raises: a lost observation is acceptable."""
    if kind not in KINDS or not text.strip():
        logger.warning("scratchpad: ignored observation of kind %r", kind)
        return False
    try:
        store = read(memory_dir)
        _bump(store, kind, text, reason, untrusted, (now or datetime.now(UTC)))
        write(memory_dir, store)
        return True
    except (OSError, TypeError, ValueError):
        logger.warning("scratchpad: could not record in %s", memory_dir, exc_info=True)
        return False


def record_dead_end(
    memory_dir: Path,
    approach: str,
    reason: str = "",
    untrusted: bool = False,
    now: datetime | None = None,
) -> bool:
    """Record an abandoned approach and mark the signals that led to it.

    Signals seen within ABANDON_WINDOW before now belong to the abandoned
    approach: all their observations so far are tied to it (``ab = n``).
    """
    if not approach.strip():
        return False
    now = now or datetime.now(UTC)
    try:
        store = read(memory_dir)
        for kind, entries in store.items():
            if kind == "dead_end":
                continue
            for entry in entries.values():
                try:
                    last = datetime.fromisoformat(str(entry.get("last")))
                except ValueError:
                    continue
                if now - last <= timedelta(seconds=ABANDON_WINDOW):
                    entry["ab"] = int(entry.get("n", 0))
        _bump(store, "dead_end", approach, reason, untrusted, now)
        write(memory_dir, store)
        return True
    except (OSError, TypeError, ValueError):
        logger.warning("scratchpad: could not record in %s", memory_dir, exc_info=True)
        return False


def dead_end_context(memory_dir: Path, limit: int = 800, now: datetime | None = None) -> str:
    """Whole dead-end lines, most recent first, that are at least partly trusted."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    expire = timedelta(days=windows()[1])
    ranked = []
    for entry in read(memory_dir).get("dead_end", {}).values():
        try:
            last = datetime.fromisoformat(str(entry["last"]))
            n, bad = int(entry["n"]), int(entry.get("bad", 0))
        except (KeyError, ValueError, TypeError):
            continue
        if n - bad >= 1 and now - last < expire:
            why = "n/a" if entry.get("rb") else entry.get("reason") or "n/a"
            line = f"- {entry.get('text', '')} (why: {why}; seen {n}x)\n"
            ranked.append((last, n, line))
    out = ""
    for _, _, line in sorted(ranked, reverse=True):
        if len(out) + len(line) <= limit:
            out += line
    return out
