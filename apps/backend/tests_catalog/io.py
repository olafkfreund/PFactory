"""Atomic IO helpers for the tests catalog — Task 3 (#19).

Provides ``load_catalog`` and ``save_catalog`` with a crash-safe atomic-write
pattern (write to ``.json.tmp`` then ``os.replace`` to the final name).

The catalog path is always::

    <repo_root>/.pfactory/tests-catalog.json

The ``.pfactory/`` directory is created automatically if absent.

Usage::

    from pathlib import Path
    from tests_catalog.io import load_catalog, save_catalog

    repo_root = Path("/path/to/aifactory-repo")

    # Read (returns None if catalog does not exist yet)
    catalog = load_catalog(repo_root)

    if catalog is not None:
        # Modify and persist
        save_catalog(repo_root, catalog)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .schema import CatalogError, TestsCatalog

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CATALOG_DIR = ".pfactory"
_CATALOG_FILE = "tests-catalog.json"
_CATALOG_TMP = "tests-catalog.json.tmp"


def _catalog_path(repo_root: Path) -> Path:
    """Return the canonical catalog path for *repo_root*."""
    return repo_root / _CATALOG_DIR / _CATALOG_FILE


def _catalog_tmp_path(repo_root: Path) -> Path:
    """Return the temporary write path for the atomic-write dance."""
    return repo_root / _CATALOG_DIR / _CATALOG_TMP


# ---------------------------------------------------------------------------
# load_catalog
# ---------------------------------------------------------------------------


def load_catalog(repo_root: Path) -> TestsCatalog | None:
    """Load ``.pfactory/tests-catalog.json`` from *repo_root*.

    The catalog is optional — it does not exist on a project's first PFactory
    run.  This function returns ``None`` in that case so callers can
    distinguish "no catalog" from "empty catalog".

    Args:
        repo_root: Root directory of the AIFactory repo (must be an existing
            directory, but the catalog file itself need not exist).

    Returns:
        A ``TestsCatalog`` instance if the file exists and parses correctly,
        or ``None`` if the file is absent.

    Raises:
        CatalogError: With ``field="file"`` if the file exists but contains
            malformed JSON or does not match the expected schema.
    """
    path = _catalog_path(repo_root)
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogError("file", f"malformed JSON at {path}: {exc}") from exc
    except OSError as exc:
        raise CatalogError("file", f"cannot read {path}: {exc}") from exc

    return TestsCatalog.from_dict(data)


# ---------------------------------------------------------------------------
# save_catalog
# ---------------------------------------------------------------------------


def save_catalog(repo_root: Path, catalog: TestsCatalog) -> Path:
    """Atomically persist *catalog* to ``.pfactory/tests-catalog.json``.

    Uses a write-to-tmp-then-rename pattern so that a crash during the write
    never leaves a partial / corrupt catalog on disk.  On POSIX systems
    ``os.replace`` is atomic within the same filesystem.

    The ``.pfactory/`` directory is created if it does not exist.

    The JSON output uses ``indent=2``, ``sort_keys=True``, and
    ``ensure_ascii=False`` for deterministic, human-readable output.  Saving
    the same catalog twice produces byte-identical files.

    Args:
        repo_root: Root directory of the AIFactory repo.
        catalog: The ``TestsCatalog`` to persist.

    Returns:
        The final ``Path`` of the written catalog file.

    Raises:
        OSError: If the directory cannot be created or the file cannot be
            written.
    """
    catalog_dir = repo_root / _CATALOG_DIR
    catalog_dir.mkdir(parents=True, exist_ok=True)

    final_path = _catalog_path(repo_root)
    tmp_path = _catalog_tmp_path(repo_root)

    payload = json.dumps(catalog.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)

    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, final_path)

    return final_path
