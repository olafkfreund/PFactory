"""Planning pipeline API — drives the portal flow (#20).

Thin FastAPI wrapper over the shared :data:`plan.service.SERVICE` singleton:
ingest → process → approve/reject → emit. State lives in the service's in-memory
store keyed by ``session_id`` (== the plan id). All emission is dry-run by default.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from plan.service import SERVICE, PlanServiceError  # noqa: E402

router = APIRouter(prefix="/api/plan/sessions", tags=["plan-pipeline"])


class IngestTextBody(BaseModel):
    text: str
    title: str | None = None
    channel: str = "portal"
    category: str = ""   # intake category (#E)
    template: str = ""   # selected template — its policy is enforced (#E)


class ApproveBody(BaseModel):
    approver: str
    feedback: str | None = None


class RejectBody(BaseModel):
    approver: str
    feedback: str


class EmitBody(BaseModel):
    repo: str
    dry_run: bool = True


def _session_dict(session) -> dict:
    # board_state() is a derived method, not a serialised field — add it so the
    # board + detail views always have the kanban column (#5).
    data = session.model_dump()
    data["board_state"] = session.board_state()
    return data


@router.get("")
async def list_sessions() -> dict:
    return {"sessions": SERVICE.list_sessions()}


@router.post("/ingest-text")
async def ingest_text(body: IngestTextBody) -> dict:
    try:
        session = SERVICE.ingest_text(
            body.text, title=body.title, channel=body.channel,
            category=body.category, template=body.template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_dict(session)


@router.post("/ingest")
async def ingest_upload(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    category: str = Form(""),
    template: str = Form(""),
) -> dict:
    data = await file.read()
    try:
        session = SERVICE.ingest_bytes(
            data, filename=file.filename or "plan.md", title=title,
            category=category, template=template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_dict(session)


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    try:
        return _session_dict(SERVICE.get(session_id))
    except PlanServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{session_id}/process")
async def process(session_id: str) -> dict:
    try:
        return _session_dict(SERVICE.process(session_id))
    except PlanServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{session_id}/approve")
async def approve(session_id: str, body: ApproveBody) -> dict:
    try:
        return _session_dict(SERVICE.approve(session_id, approver=body.approver,
                                             feedback=body.feedback))
    except PlanServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:  # ApprovalError (gates not passed)
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{session_id}/reject")
async def reject(session_id: str, body: RejectBody) -> dict:
    try:
        return _session_dict(SERVICE.reject(session_id, approver=body.approver,
                                            feedback=body.feedback))
    except PlanServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/emit")
async def emit(session_id: str, body: EmitBody) -> dict:
    try:
        return _session_dict(SERVICE.emit(session_id, repo=body.repo, dry_run=body.dry_run))
    except PlanServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
