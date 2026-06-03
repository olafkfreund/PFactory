"""PFactory v0.2 portal endpoints: framework registry.

Serves the framework registry's contents to the portal:
  GET /api/pfactory/frameworks                — list of names + summary
  GET /api/pfactory/frameworks/{name}         — full FrameworkDescriptor

Read-only. Returns 404 for unknown framework names.

Note on imports: FastAPI is only present at runtime (inside the web-server
venv). This module is unit-tested without FastAPI installed; the test suite
injects a minimal stub. No ``from fastapi import ...`` at module top — the
router, HTTPException, and Response objects are resolved via sys.modules at
the point when this module is loaded.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status as http_status


router = APIRouter()


# ─── Path resolution ─────────────────────────────────────────────────────────


def _resolve_frameworks_dir() -> Path | None:
    """Return the repo-root ``frameworks/`` directory, or None if not found.

    Walks up from this file:
        pfactory_frameworks.py
        → server/routes/
        → server/
        → apps/web-server/
        → apps/
        → <repo-root>/frameworks/
    """
    here = Path(__file__).resolve()
    # here is: <repo>/apps/web-server/server/routes/pfactory_frameworks.py
    # parents[0] = routes/
    # parents[1] = server/
    # parents[2] = apps/web-server/
    # parents[3] = apps/
    # parents[4] = <repo-root>
    candidate = here.parents[4] / "frameworks"
    if candidate.is_dir():
        return candidate
    return None


# ─── Validation ───────────────────────────────────────────────────────────────


_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_name(name: str) -> None:
    """Reject path-traversal attempts in the ``{name}`` path parameter."""
    if not name or not _NAME_RE.match(name):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"invalid framework name: {name!r}",
        )


# ─── Serialisation ────────────────────────────────────────────────────────────


def _descriptor_to_dict(desc: Any) -> dict:
    """Serialise a FrameworkDescriptor to a JSON-safe dict.

    Uses ``dataclasses.asdict`` where the descriptor is a dataclass, then
    converts non-serialisable sub-objects (tuples of Lanes, RuntimeSpec) into
    their primitive equivalents.
    """
    raw = asdict(desc)
    # Convert Lane enum members (they are stored as objects in the dataclass
    # fields represented as tuple[Lane, ...]) to their string values.
    if "lanes" in raw:
        raw["lanes"] = [
            (v.value if hasattr(v, "value") else str(v)) for v in raw["lanes"]
        ]
    # RuntimeSpec is a nested dataclass; asdict handles it automatically.
    return raw


def _summary_row(name: str, desc: Any) -> dict:
    """Compact row for the list endpoint."""
    return {
        "name": name,
        "language": desc.language,
        "coverage_strategy": desc.coverage_strategy,
        "lanes": [
            (v.value if hasattr(v, "value") else str(v)) for v in desc.lanes
        ],
        "version_range": desc.version_range,
        "template_count": len(desc.templates),
    }


# ─── Registry loader ──────────────────────────────────────────────────────────


def _load_registry(frameworks_dir: Path | None = None) -> dict:
    """Load the framework registry, importing from the backend package.

    Adds ``apps/backend`` to ``sys.path`` if needed so the import resolves
    without requiring the backend to be installed as a package.
    """
    # Resolve the backends path relative to this file
    here = Path(__file__).resolve()
    backend_path = str(here.parents[4] / "apps" / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    import framework_registry  # noqa: PLC0415

    fdir = frameworks_dir or _resolve_frameworks_dir()
    return framework_registry.load_registry(frameworks_dir=fdir)


# ─── Endpoints ────────────────────────────────────────────────────────────────


import json as _json  # noqa: E402


@router.get("")
def list_frameworks() -> dict:
    """List all registered frameworks — name, language, lanes, coverage strategy.

    Response shape::

        {
          "frameworks": [
            {
              "name": "pytest",
              "language": "python",
              "coverage_strategy": "cobertura",
              "lanes": ["unit"],
              "version_range": ">=7.0,<9.0",
              "template_count": 5
            },
            ...
          ],
          "count": 3
        }

    Sorted by name ascending.
    """
    try:
        registry = _load_registry()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to load framework registry: {exc}",
        ) from exc

    rows = [_summary_row(name, desc) for name, desc in sorted(registry.items())]
    return {"frameworks": rows, "count": len(rows)}


@router.get("/{name}")
def get_framework(name: str) -> Response:
    """Return the full FrameworkDescriptor for *name*.

    Returns 400 on path-traversal attempt, 404 if the name is unknown.

    Response shape::

        {
          "name": "playwright",
          "language": "typescript",
          "lanes": ["browser"],
          "version_range": ">=1.40,<2.0",
          "runtime": {"image": "...", "entrypoint": [...]},
          "manifest_signals": [...],
          "test_path_conventions": [...],
          "templates": [...],
          "coverage_strategy": "skip",
          "context_block": "...",
          "evaluator_hooks": [...]
        }
    """
    _validate_name(name)

    try:
        registry = _load_registry()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to load framework registry: {exc}",
        ) from exc

    if name not in registry:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"framework not found: {name!r}",
        )

    desc = registry[name]
    payload = _descriptor_to_dict(desc)
    return Response(
        content=_json.dumps(payload),
        media_type="application/json",
        status_code=200,
    )
