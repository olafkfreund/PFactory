"""Shared, symlink-safe filesystem walk for the recon probes.

Dependency-free so any probe module can import it without an import cycle.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def iter_files(root: Path, suffixes: tuple[str, ...], max_files: int) -> list[Path]:
    """Static, symlink-safe walk for files with the given suffixes.

    Skips ``.git``, never follows symlinks out of ``root``, and stops after
    ``max_files`` entries have been examined (each probe passes its own cap).
    """
    out: list[Path] = []
    root = root.resolve()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            count += 1
            if count > max_files:
                return out
            if name.endswith(suffixes):
                fp = Path(dirpath) / name
                # confine to the clone; never follow symlinks out of it
                if fp.is_symlink():
                    continue
                with contextlib.suppress(OSError):
                    if root in fp.resolve().parents or fp.resolve() == root:
                        out.append(fp)
    return out
