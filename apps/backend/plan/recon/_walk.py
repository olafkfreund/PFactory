"""Shared, symlink-safe filesystem walk for the recon probes.

Dependency-free so any probe module can import it without an import cycle.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

# Dirs no static code-reader should descend into: VCS metadata, virtualenvs,
# vendored third-party code, caches, and build output. Shared by the
# plan/recon and plan/detect walkers so the exclusion list lives in exactly
# one place. NOTE: ``.github`` must stay walkable (ci_probe reads workflow
# files), so dot-dirs are excluded by name here, not wholesale.
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        "site-packages",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".nox",
        ".eggs",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".cache",
        ".direnv",
        ".terraform",
        ".idea",
        ".vscode",
        ".aifactory",
    }
)


def is_excluded_dir(name: str) -> bool:
    """True for vendored/venv/cache dirs that static code-readers must skip."""
    return name in EXCLUDED_DIRS or name.startswith((".venv", "venv"))


def prune_dirnames(dirnames: list[str]) -> None:
    """In-place prune of an ``os.walk`` dirnames list against the exclusions."""
    dirnames[:] = [d for d in dirnames if not is_excluded_dir(d)]


def iter_files(root: Path, suffixes: tuple[str, ...], max_files: int) -> list[Path]:
    """Static, symlink-safe walk for files with the given suffixes.

    Skips excluded dirs (VCS/venv/vendored/cache), never follows symlinks out
    of ``root``, and stops after ``max_files`` entries have been examined
    (each probe passes its own cap).
    """
    out: list[Path] = []
    root = root.resolve()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        prune_dirnames(dirnames)
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
