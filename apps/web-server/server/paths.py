"""Centralized path helpers for PFactory data directory."""
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

AI_FACTORY_DIR = Path.home() / ".pfactory"


def atomic_write_secret_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically, mode 0600 (#298).

    Replaces the ``path.write_text(json.dumps(...))`` + ``path.chmod(0o600)``
    pattern, which had two defects on files holding OAuth tokens / API keys:

    1. **Not atomic.** ``write_text`` truncates in place, so a concurrent reader
       sees a torn file. ``load_profiles`` swallows the resulting
       ``JSONDecodeError`` and returns ``{"profiles": []}`` — so the reader
       believes there are NO profiles, and the next save writes that belief back,
       permanently destroying every profile and its token. Reproduced against the
       real load/save pair: a torn read inside 3 seconds of contention.
    2. **A world-readable window.** ``write_text`` creates with the default umask
       (typically 0644) and only chmods to 0600 *after* the secrets are on disk.

    ``mkstemp`` creates at 0600 from birth, and ``os.replace`` is atomic within a
    filesystem: a reader sees the whole old file or the whole new one, never half.
    The temp file is created in the destination directory so the rename cannot
    cross a filesystem boundary.

    NOTE: this makes each write atomic; it does NOT serialise read-modify-write.
    Two concurrent handlers can still lose an update (last writer wins) — but the
    file is always valid, so a lost update costs one field, not every token.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp).replace(path)  # atomic within a filesystem
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def migrate_legacy_data():
    """Safely migrate legacy PFactory data folder to PFactory."""
    legacy_dir = Path.home() / ".pfactory"
    if legacy_dir.exists() and not AI_FACTORY_DIR.exists():
        try:
            shutil.copytree(legacy_dir, AI_FACTORY_DIR, dirs_exist_ok=True)
            print(f"PFactory - Successfully migrated legacy data from {legacy_dir} to {AI_FACTORY_DIR}")
        except Exception as e:
            print(f"PFactory - Warning: failed to migrate legacy data: {e}")


# Run migration automatically on module load
migrate_legacy_data()


def get_data_dir() -> Path:
    """Return the PFactory data directory, creating it if needed."""
    AI_FACTORY_DIR.mkdir(parents=True, exist_ok=True)
    return AI_FACTORY_DIR


def get_data_file(filename: str) -> Path:
    """Get a file path in the PFactory data directory."""
    return get_data_dir() / filename
