"""Read-only meta API — registry, templates, providers, adapters (#20/#25).

Backs the portal's management screens. Everything here is read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

router = APIRouter(prefix="/api/plan/meta", tags=["plan-meta"])


@router.get("/registry")
async def registry() -> dict:
    from plan.registry import load_registry

    reg = load_registry()
    return {"entries": [e.model_dump() for e in reg.entries]}


@router.get("/templates")
async def templates() -> dict:
    from plan.templates import load_templates

    out = []
    for name, tmpl in load_templates().items():
        out.append({
            "name": name,
            "title": tmpl.metadata.title,
            "category": getattr(tmpl.metadata, "category", ""),
            "description": tmpl.metadata.description,
            "tags": tmpl.metadata.tags,
            "policy": tmpl.policy.model_dump(),
        })
    return {"templates": out}


@router.get("/categories")
async def categories() -> dict:
    """Plan categories for the intake picker (#1): each category with its plan
    types (+ stage toggles) and any matching templates. Shares the single source
    with the MCP ``plan_categories`` tool so the portal and agents see the same."""
    from plan.agent_api import plan_categories

    return plan_categories()


@router.get("/providers")
async def providers() -> dict:
    from plan.providers.base import available_providers

    # Importing the provider modules registers them.
    for mod in ("aws", "azure", "gcp", "terraform"):
        try:
            __import__(f"plan.providers.{mod}")
        except Exception:
            pass
    return {"providers": available_providers()}


@router.get("/adapters")
async def adapters() -> dict:
    from plan.enrich.base import available_adapters
    from plan.enrich.knowledge.base import available_connectors

    for mod in ("kubernetes", "openshift", "azure", "aws", "gcp"):
        try:
            __import__(f"plan.enrich.adapters.{mod}")
        except Exception:
            pass
    for mod in ("git_markdown", "backstage", "confluence", "gitbook", "notion"):
        try:
            __import__(f"plan.enrich.knowledge.{mod}")
        except Exception:
            pass
    return {
        "infra_adapters": available_adapters(),
        "knowledge_connectors": available_connectors(),
    }
