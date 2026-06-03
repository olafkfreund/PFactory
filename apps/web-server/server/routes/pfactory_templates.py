"""PFactory v0.2 portal endpoints: template library.

Serves the per-framework template catalogue to the portal:
  GET /api/pfactory/templates?framework={name}      — list templates for a framework
  GET /api/pfactory/templates/{framework}/{name}    — single template body + metadata

Read-only. Returns 404 for unknown framework names or unknown template names.

Note on imports: FastAPI is only present at runtime (inside the web-server
venv). This module is unit-tested without FastAPI installed; the test suite
injects a minimal stub before importing this module.
"""

from __future__ import annotations

import json as _json
import re
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status as http_status


router = APIRouter()


# ─── Path resolution ─────────────────────────────────────────────────────────


def _resolve_repo_root() -> Path:
    """Return the repository root.

    This file lives at:
        <repo>/apps/web-server/server/routes/pfactory_templates.py
    parents[4] = <repo>
    """
    return Path(__file__).resolve().parents[4]


# ─── Validation ───────────────────────────────────────────────────────────────


_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_segment(value: str, field: str) -> None:
    """Reject path-traversal attempts in URL path/query segments."""
    if not value or not _NAME_RE.match(value):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"invalid {field}: {value!r}",
        )


# ─── Backend import ───────────────────────────────────────────────────────────


def _ensure_backend_on_path() -> None:
    """Add apps/backend to sys.path if not already present."""
    backend_path = str(_resolve_repo_root() / "apps" / "backend")
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


def _load_templates_for_framework(framework_name: str) -> dict:
    """Call templates_pkg.load_templates_for_framework and return result."""
    _ensure_backend_on_path()
    import templates_pkg  # noqa: PLC0415
    return templates_pkg.load_templates_for_framework(framework_name)


# ─── Serialisation ────────────────────────────────────────────────────────────


def _metadata_to_dict(meta) -> dict:
    """Serialise a TemplateMetadata dataclass to a plain dict."""
    return {
        "description": meta.description,
        "requires_target": meta.requires_target,
        "requires_auth": meta.requires_auth,
        "vars": list(meta.vars),
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("")
def list_templates(request) -> Response:
    """List all templates for the given framework.

    Reads the ``framework`` query parameter (required). Returns 400 if the
    param is absent or contains traversal characters. Returns 404 if the
    framework directory has no templates (unknown framework).

    Response shape::

        {
          "framework": "pytest",
          "templates": [
            {
              "name": "function-pure.py.tmpl",
              "metadata": {
                "description": "...",
                "requires_target": false,
                "requires_auth": false,
                "vars": [...]
              }
            },
            ...
          ],
          "count": 5
        }
    """
    # Extract ``framework`` from query params (duck-typed request object)
    if hasattr(request, "query_params"):
        framework = request.query_params.get("framework", "")
    else:
        # Plain dict passed in tests
        framework = request.get("framework", "")

    if not framework:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="required query param 'framework' is missing",
        )
    _validate_segment(framework, "framework")

    templates = _load_templates_for_framework(framework)

    if not templates:
        # Empty dict means either the framework doesn't exist or has no
        # templates dir — both map to 404 for the portal.
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"no templates found for framework: {framework!r}",
        )

    rows = [
        {"name": tname, "metadata": _metadata_to_dict(tf.metadata)}
        for tname, tf in sorted(templates.items())
    ]
    payload = {"framework": framework, "templates": rows, "count": len(rows)}
    return Response(
        content=_json.dumps(payload),
        media_type="application/json",
        status_code=200,
    )


@router.get("/{framework}/{name}")
def get_template(framework: str, name: str) -> Response:
    """Return the full body + metadata for a single template.

    Returns 400 on path-traversal in either segment, 404 if the framework
    or template name is unknown.

    Response shape::

        {
          "name": "login-flow.spec.ts.tmpl",
          "framework": "playwright",
          "metadata": {
            "description": "Login flow ...",
            "requires_target": true,
            "requires_auth": false,
            "vars": ["target_base_url", "test_name", ...]
          },
          "body": "import { test, expect } from '@playwright/test';\\n..."
        }
    """
    _validate_segment(framework, "framework")
    _validate_segment(name, "name")

    templates = _load_templates_for_framework(framework)

    if not templates:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"no templates found for framework: {framework!r}",
        )

    if name not in templates:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"template {name!r} not found in framework {framework!r}",
        )

    tf = templates[name]
    payload = {
        "name": name,
        "framework": framework,
        "metadata": _metadata_to_dict(tf.metadata),
        "body": tf.body,
    }
    return Response(
        content=_json.dumps(payload),
        media_type="application/json",
        status_code=200,
    )
