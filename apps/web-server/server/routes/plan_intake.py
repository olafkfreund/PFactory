"""Plan intake router — portal entry points for plan ingestion (issue #4).

Two doors into the same :mod:`plan.ingest.channels` layer the CLI and MCP tool
use:

  * ``POST /api/plan/ingest-text`` — JSON ``{text, title?, channel?}`` for
    pasted acceptance-criteria text.
  * ``POST /api/plan/ingest`` — multipart file upload (PDF / DOCX / markdown);
    the upload is flattened in memory via ``ingest_bytes`` (no disk write).

Both return the :class:`~plan.models.NormalizedPlan` as a dict. Parse failures
become ``400`` responses.

Wiring: ``include_router(plan_intake.router)`` in ``server/main.py`` (the
router already carries its ``/api/plan`` prefix). This file lives in the
web-server app, so importing FastAPI at module top is fine.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

# Add the backend dir to sys.path so the plan.ingest channels resolve.
# Mirrors the pattern used by mcp.py / auto_fix.py et al.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from plan.ingest import channels  # noqa: E402  (after sys.path insert)
from plan.ingest.document_loaders import DocumentLoadError  # noqa: E402
from spec_sources import SpecSourceError  # noqa: E402

router = APIRouter(prefix="/api/plan", tags=["plan-intake"])


class IngestTextRequest(BaseModel):
    """Body for ``POST /api/plan/ingest-text``."""

    text: str
    title: str | None = None
    channel: str = "portal"


@router.post("/ingest-text")
async def ingest_text(body: IngestTextRequest) -> dict:
    """Ingest pasted acceptance-criteria text into a NormalizedPlan dict."""
    try:
        plan = channels.ingest_text(
            body.text,
            source_channel=body.channel,  # type: ignore[arg-type]
            title=body.title,
        )
    except (SpecSourceError, DocumentLoadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return plan.model_dump()


@router.post("/ingest")
async def ingest_upload(
    file: UploadFile = File(...),
    title: str | None = Form(None),
) -> dict:
    """Ingest an uploaded plan document (PDF / DOCX / text) into a plan dict."""
    data = await file.read()
    filename = file.filename or "upload"
    try:
        plan = channels.ingest_bytes(
            data,
            filename=filename,
            source_channel="portal",
            title=title,
        )
    except (SpecSourceError, DocumentLoadError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return plan.model_dump()
