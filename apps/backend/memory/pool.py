"""Merge one memory directory into another without losing newer data (#3795).

``shutil.copytree`` replaces files wholesale, so a stale spec snapshot copied
into the project pool erased newer counts, trust marks and gotchas. This merges
instead, under the destination's memory lock.
"""

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import retention, scratchpad
from .locking import LOCK_NAME, locked

logger = logging.getLogger(__name__)
GOTCHAS_HEADER = "# Gotchas & Pitfalls\n\nThings to watch out for in this codebase.\n"
PATTERNS_HEADER = "# Code Patterns\n\nEstablished patterns to follow in this codebase:\n"


def _later(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """The entry with the higher count (ties: the later one); trust and abandon
    marks seen by either side survive, so a snapshot cannot launder a signal."""
    first, second = sorted(
        (a, b), key=lambda e: (int(e.get("n", 0)), str(e.get("last", ""))), reverse=True
    )
    out = dict(first)
    for mark in ("bad", "ab"):
        out[mark] = max(int(a.get(mark, 0)), int(b.get(mark, 0)))
    for keep in ("promoted", "body", "pn"):
        if keep not in out and keep in second:
            out[keep] = second[keep]
    return out


def _merge_scratchpad(src: Path, dst: Path) -> None:
    merged = scratchpad.read(dst)
    for kind, entries in scratchpad.read(src).items():
        mine = merged.setdefault(kind, {})
        for key, entry in entries.items():
            mine[key] = _later(mine[key], entry) if key in mine else entry
        while len(mine) > scratchpad.MAX_KEYS:
            del mine[
                min(
                    mine,
                    key=lambda k: (int(mine[k].get("n", 0)), str(mine[k].get("last"))),
                )
            ]
    scratchpad.write(dst, merged)


def _merge_confirmed(src: Path, dst: Path) -> None:
    merged = retention._read(dst)
    for key, stamp in retention._read(src).items():
        merged[key] = max(str(stamp), str(merged.get(key, "")))
    with tempfile.NamedTemporaryFile("w", dir=dst, delete=False) as f:
        json.dump(merged, f)
    Path(f.name).replace(dst / "confirmed.json")


def _atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as f:
        f.write(text)
    Path(f.name).replace(path)


def _merge_notes(src: Path, dst: Path, header: str) -> None:
    """Union of timestamped entries and of whole bullet lines (which live before
    the first heading, where ``gotchas_context`` reads them). A bullet is one
    line, trust marker included. Also used for ``patterns.md``."""
    mine, theirs = (dst.read_text() if dst.exists() else ""), src.read_text()
    first = retention.HEADING.search(mine)
    head, rest = (mine[: first.start()], mine[first.start() :]) if first else (mine, "")
    theirs_first = retention.HEADING.search(theirs)
    theirs_head = theirs[: theirs_first.start()] if theirs_first else theirs
    have = {line.strip() for line in head.splitlines()}
    bullets = [
        s
        for line in theirs_head.splitlines()
        if (s := line.strip()).startswith("- ") and s not in have
    ]
    known = {body for _, body, _ in retention.split_gotchas(mine)}
    entries = "".join(
        "\n" + entry.lstrip("\n")
        for entry, body, _ in retention.split_gotchas(theirs)
        if body not in known
    )
    if not (bullets or entries or not dst.exists()):
        return
    if not head.strip():
        head = header
    head += "" if head.endswith("\n") else "\n"
    _atomic_write(dst, head + "".join(f"{b}\n" for b in bullets) + rest + entries)


def _merge_map(src: Path, dst: Path) -> None:
    """Union of the file maps; for a path both know, the newer file's entry wins."""
    try:
        mine = json.loads(dst.read_text()) if dst.exists() else {}
        theirs = json.loads(src.read_text())
    except ValueError:
        return  # a corrupt map is not worth losing the rest of the merge
    newer = src.stat().st_mtime > dst.stat().st_mtime if dst.exists() else True
    _atomic_write(
        dst,
        json.dumps(
            {**mine, **theirs} if newer else {**theirs, **mine},
            indent=2,
            sort_keys=True,
        ),
    )


def _merge_session(src: Path, dst: Path, seen: dict[Path, set[bytes]]) -> None:
    """Session numbers are only unique within a spec: keep both records under a
    free number instead of letting the newer file replace the other."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.parent not in seen:
        seen[dst.parent] = {p.read_bytes() for p in dst.parent.glob("session_*.json")}
    digests = seen[dst.parent]
    data = src.read_bytes()
    if data in digests:
        return
    if dst.exists():
        taken = {p.name for p in dst.parent.glob("session_*.json")}
        n = len(taken) + 1
        while (name := f"session_{n:03d}.json") in taken:
            n += 1
        dst = dst.parent / name
    dst.write_bytes(data)
    digests.add(data)


def merge_into(src: Path, dst: Path, same_spec: bool = False) -> None:
    """Merge memory directory ``src`` into ``dst``. Raises OSError (including
    LockTimeoutError); callers log and carry on.

    ``same_spec``: both sides are the same spec's memory (worktree to source), so
    a rewritten session file is the build's own and the newer one wins. Across
    specs (the pool) session numbers collide and both records are kept."""
    logger.debug("merging memory %s into %s", src, dst)
    dst.mkdir(parents=True, exist_ok=True)
    seen: dict[Path, set[bytes]] = {}
    with locked(dst):
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            target = dst / rel
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.name == LOCK_NAME:
                continue
            elif rel.as_posix() == scratchpad.FILENAME:
                _merge_scratchpad(src, dst)
            elif rel.as_posix() == "confirmed.json":
                _merge_confirmed(src, dst)
            elif rel.as_posix() == "gotchas.md":
                _merge_notes(path, target, GOTCHAS_HEADER)
            elif rel.as_posix() == "patterns.md":
                _merge_notes(path, target, PATTERNS_HEADER)
            elif rel.as_posix() == "codebase_map.json":
                _merge_map(path, target)
            elif (
                not same_spec and path.parent.name == "session_insights" and path.suffix == ".json"
            ):
                _merge_session(path, target, seen)
            elif not target.exists() or path.stat().st_mtime > target.stat().st_mtime:
                shutil.copy2(path, target)


# ── project pool (#3795) ─────────────────────────────────────────────────────
# The pool is a directory beside the project's specs, derived from the project
# directory and nothing else: no environment variable, no listing of siblings,
# so one project can never read another's dead ends.

BRAND = ".pfactory"


def pool_dir(project_dir: Path) -> Path:
    return project_dir / BRAND / "memory"


def pool_dir_from_spec(source_spec_dir: Path) -> Path:
    """Same directory as :func:`pool_dir` for a spec under the project
    (`<project>/pfactory/specs/<id>`). `project_dir` itself may be a worktree or a
    job clone, so the sync anchors here, as AIFactory does."""
    return source_spec_dir.parent.parent / "memory"


def seed(project_dir: Path | None, spec_dir: Path) -> bool:
    """Merge the project's pool into a spec's memory. Never raises."""
    if not project_dir or not pool_dir(project_dir).is_dir():
        return False
    try:
        merge_into(pool_dir(project_dir), spec_dir / "memory")
        return True
    except (OSError, shutil.Error):
        logger.warning("could not seed memory from the project pool", exc_info=True)
        return False


def sync(spec_dir: Path, source_spec_dir: Path | None) -> bool:
    """Merge a spec's memory into the project's pool. Never raises."""
    if not source_spec_dir or not (spec_dir / "memory").is_dir():
        return False
    try:
        merge_into(spec_dir / "memory", pool_dir_from_spec(source_spec_dir))
        return True
    except (OSError, shutil.Error):
        logger.warning("could not sync memory to the project pool", exc_info=True)
        return False
