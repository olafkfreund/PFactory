"""Minimal PFactory inspection server.

Mounts ONLY the Plan Factory routers (intake + pipeline + meta) on a bare
FastAPI app — no database, auth, or the rest of the web-server machinery — so the
planning pipeline can be driven and inspected locally without the full stack.

    /tmp/pf-serve-venv/bin/python scripts/inspect_server.py   # serves :3114

Endpoints under /api/plan/* ; OpenAPI docs at /docs ; health at /health.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "backend"))
sys.path.insert(0, str(ROOT / "apps" / "web-server"))

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

_ROUTES_DIR = ROOT / "apps" / "web-server" / "server" / "routes"


def _load(module_name: str):
    """Load a route module by file path (avoids importing the server package)."""
    spec = importlib.util.spec_from_file_location(module_name, _ROUTES_DIR / f"{module_name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


plan_intake = _load("plan_intake")
plan_pipeline = _load("plan_pipeline")
plan_meta = _load("plan_meta")

app = FastAPI(
    title="PFactory — Plan Factory (inspection server)",
    description="Plan ingestion + governance pipeline, mounted standalone.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(plan_intake.router)
app.include_router(plan_pipeline.router)
app.include_router(plan_meta.router)


@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    # The portal's login validates a token against /api/health; this server
    # has no auth, so any (or no) token is accepted — fine for local inspect.
    return {"status": "ok", "service": "pfactory-plan-factory"}


@app.get("/api/settings")
def settings() -> dict:
    # The portal's checkAuth() validates the stored token against /api/settings
    # on every load. Returning 200 keeps the dev session "authenticated".
    return {"theme": "system", "auth": "disabled (inspection server)"}


@app.get("/")
def index() -> dict:
    return {
        "service": "PFactory — Plan Factory",
        "docs": "/docs",
        "endpoints": [
            "POST /api/plan/sessions/ingest-text",
            "POST /api/plan/sessions/ingest",
            "GET  /api/plan/sessions",
            "POST /api/plan/sessions/{id}/process",
            "POST /api/plan/sessions/{id}/approve",
            "POST /api/plan/sessions/{id}/emit",
            "GET  /api/plan/meta/registry|templates|providers|adapters",
        ],
    }


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.environ.get("PFACTORY_INSPECT_PORT", "3188"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
