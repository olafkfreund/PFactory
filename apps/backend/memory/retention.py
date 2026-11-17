"""Caps and read-time expiry for file-backed session memory."""

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CAPS = {"build": 20, "planning": 5, "verification": 8, "insights": 5, "docs": 0}
DEFAULT_SESSION_TYPE = "planning"
# Agent types that map to a cap; unlisted ones keep the env/default type.
AGENT_SESSION_TYPES = {
    "coder": "build",
    "qa_reviewer": "verification",
    "qa_fixer": "verification",
    "insights": "insights",
}
DEMOTE_DAYS = 30
EXPIRE_DAYS = 90
HEADING = re.compile(r"(?m)^## \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\r?\n")


def session_type(explicit: str | None = None) -> str:
    name = (
        explicit if explicit is not None else os.getenv("MEMORY_SESSION_TYPE", DEFAULT_SESSION_TYPE)
    )
    if name not in CAPS:
        logger.warning("unknown memory session type %r; using %s", name, DEFAULT_SESSION_TYPE)
        return DEFAULT_SESSION_TYPE
    return name


def windows() -> tuple[int, int]:
    try:
        demote = int(os.getenv("MEMORY_DEMOTE_DAYS", str(DEMOTE_DAYS)))
        expire = int(os.getenv("MEMORY_EXPIRE_DAYS", str(EXPIRE_DAYS)))
        if 0 < demote < expire:
            return demote, expire
    except ValueError:
        logger.debug("ignoring non-integer MEMORY_DEMOTE_DAYS/MEMORY_EXPIRE_DAYS")
    return DEMOTE_DAYS, EXPIRE_DAYS


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _read(memory_dir: Path) -> dict[str, str]:
    try:
        data = json.loads((memory_dir / "confirmed.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def confirm(memory_dir: Path, texts: list[str], now: datetime | None = None) -> None:
    """Stamp last_confirmed_at; never raises (a lost stamp only demotes earlier)."""
    if not texts:
        return
    path = None
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        data = _read(memory_dir)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        for item in texts:
            data[_key(item)] = timestamp
        with tempfile.NamedTemporaryFile("w", dir=memory_dir, delete=False) as f:
            path = Path(f.name)
            json.dump(data, f)
        path.replace(memory_dir / "confirmed.json")
    except OSError:
        logger.warning("could not record confirmed.json in %s", memory_dir, exc_info=True)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def split_gotchas(text: str) -> list[tuple[str, str, datetime]]:
    matches = list(HEADING.finditer(text))
    entries = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        try:
            heading_time = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except ValueError:
            continue
        entries.append((text[match.start() : end], text[match.end() : end].strip(), heading_time))
    return entries


def _last_confirmed(entry_time: datetime, body: str, confirmed: dict[str, str]) -> datetime:
    last = entry_time
    try:
        value = confirmed.get(_key(body))
        if isinstance(value, str):
            last = max(
                last,
                datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC),
            )
    except ValueError:
        pass
    return last


def _pack(chunks: list[tuple[int, str]], budget: int) -> list[tuple[int, str]]:
    """Whole chunks, best rank first (chunks arrive ranked), skipping any that
    does not fit; returned in file order (the first tuple item)."""
    kept = []
    for pos, chunk in chunks:
        if len(chunk) <= budget:
            kept.append((pos, chunk))
            budget -= len(chunk)
    return sorted(kept)


def gotchas_context(memory_dir: Path, text: str, limit: int, now: datetime | None = None) -> str:
    """Pick gotchas for the context window by last confirmation (RFC-0021 Phase 5).

    Whole entries, newest-confirmed first, shown in file order. No fragments
    unless a single top entry is larger than the window (then its tail).
    """
    entries = split_gotchas(text)
    if not entries:
        return text[-limit:]
    now = (now or datetime.now(UTC)).astimezone(UTC)
    demote_days, expire_days = windows()
    confirmed = _read(memory_dir)
    fresh = []
    demoted = []
    for pos, (entry, body, heading_time) in enumerate(entries):
        last = _last_confirmed(heading_time, body, confirmed)
        age = now - last
        if age >= timedelta(days=expire_days):
            continue
        elif age >= timedelta(days=demote_days):
            demoted.append((last, pos, entry))
        else:
            fresh.append((last, pos, entry))
    # Text before the first heading has no timestamp (legacy bullets): never
    # expire it, rank it last.
    first = HEADING.search(text)
    preamble = text[: first.start()] if first else ""
    ranked = sorted(fresh, reverse=True)
    chunks = [(pos, entry) for _, pos, entry in ranked]
    if preamble:
        chunks.append((-1, preamble))
    kept = _pack(chunks, limit)
    if ranked and not any(pos >= 0 for pos, _ in kept):
        kept = [(0, ranked[0][2][-limit:])]
    fresh_text = "".join(c for _, c in kept)
    if not demoted:
        return fresh_text
    heading = f"\n## Low confidence (not re-observed in {demote_days} days)\n"
    remaining = limit - len(fresh_text) - len(heading)
    chunks = [(pos, entry) for _, pos, entry in sorted(demoted, reverse=True)]
    low = "".join(c for _, c in _pack(chunks, remaining))
    return fresh_text + heading + low if low else fresh_text


def escape_headings(body: str) -> str:
    """Escape header-looking lines (injective: backslashes shift by one) so a
    stored entry cannot be split in two by ``split_gotchas``."""
    return re.sub(r"(?m)^(\\*## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\])", r"\\\1", body)
