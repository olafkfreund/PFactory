"""Planning pipeline API — drives the portal flow (#20).

Thin FastAPI wrapper over the shared :data:`plan.service.SERVICE` singleton:
ingest → process → approve/reject → emit. State lives in the service's in-memory
store keyed by ``session_id`` (== the plan id). All emission is dry-run by default.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import DocsTargetConnection
from ..database.engine import get_db

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from plan.review.readiness.waiver import WaiverError  # noqa: E402
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


class WaiveBody(BaseModel):
    check_ids: list[str]
    reason: str
    waived_by: str


class EmitBody(BaseModel):
    repo: str
    dry_run: bool = True
    # Per-plan selection of docs sinks by kind (e.g. ["backstage"]). When None,
    # the caller's enabled-by-default connections decide. The repo/GitHub doc is
    # always written by the orchestrator. Only honoured when PFACTORY_DOCS_EMIT.
    docs_targets: list[str] | None = None


class EmitContractBody(BaseModel):
    repo: str | None = None
    project_id: str | None = None
    dry_run: bool = True


class _UrllibHttp:
    """Stdlib HttpClient for live contract emit (avoids an httpx dependency)."""

    def post(self, url: str, *, params: dict, json: object) -> object:
        import json as _json
        import os
        import urllib.parse
        import urllib.request

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Content-Type": "application/json"}
        # Authenticate the cross-service handoff. AIFactory's auth middleware
        # requires a bearer token on /api/*; without it the POST 401s and the
        # whole PFactory→AIFactory emit fails. PFactory holds the shared
        # APP_API_TOKEN (factory-secrets); send it.
        token = (
            os.environ.get("PFACTORY_AIFACTORY_API_TOKEN")
            or os.environ.get("APP_API_TOKEN")
            or os.environ.get("PFACTORY_MCP_SECRET")
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            url,
            data=_json.dumps(json).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
        try:
            return _json.loads(body)
        except ValueError:
            return {"raw": body}


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


@router.post("/{session_id}/waive")
async def waive(session_id: str, body: WaiveBody) -> dict:
    """Record a human waiver of hard readiness failures (#77).

    The serialized session carries the updated ``review.readiness`` so the portal
    can reflect that the waived check no longer blocks emission.
    """
    try:
        return _session_dict(SERVICE.waive(
            session_id, check_ids=body.check_ids, reason=body.reason,
            waived_by=body.waived_by,
        ))
    except (PlanServiceError, WaiverError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/reject")
async def reject(session_id: str, body: RejectBody) -> dict:
    try:
        return _session_dict(SERVICE.reject(session_id, approver=body.approver,
                                            feedback=body.feedback))
    except PlanServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _load_docs_connections(
    request: Request, db: AsyncSession
) -> list[dict] | None:
    """Best-effort load of the caller's docs-target connections (P4b).

    Returns ``None`` when no user resolves (shared-token/MCP calls) or the user
    has no connections — so the emit falls back to env-based docs resolution and
    nothing changes for the running factory. Never raises.
    """
    try:
        from .auth_routes import get_current_user

        user = await get_current_user(request, db)
        result = await db.execute(
            select(DocsTargetConnection).where(
                DocsTargetConnection.user_id == user.id
            )
        )
        conns = result.scalars().all()
    except Exception:  # noqa: BLE001 — docs wiring must never break emit
        return None
    if not conns:
        return None
    return [
        {
            "kind": c.kind,
            "base_url": c.base_url,
            "api_token": c.api_token,
            "space": c.space,
            "enabled_by_default": c.enabled_by_default,
        }
        for c in conns
    ]


@router.post("/{session_id}/emit")
async def emit(
    session_id: str,
    body: EmitBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    docs_connections = await _load_docs_connections(request, db)
    try:
        return _session_dict(
            SERVICE.emit(
                session_id,
                repo=body.repo,
                dry_run=body.dry_run,
                docs_connections=docs_connections,
                docs_selected=body.docs_targets,
            )
        )
    except PlanServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/emit-contract")
async def emit_contract(session_id: str, body: EmitContractBody) -> dict:
    """Emit the RFC-0002 signed Task Contract v2 to AIFactory (#65).

    Dry-run by default (returns the assembled+signed contract under
    ``contract_result``); a live run POSTs it to ``/api/tasks/from-plan``.
    """
    try:
        http = None if body.dry_run else _UrllibHttp()
        return _session_dict(
            SERVICE.emit_contract(
                session_id, repo=body.repo, project_id=body.project_id,
                http=http, dry_run=body.dry_run,
            )
        )
    except PlanServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
