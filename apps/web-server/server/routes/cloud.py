"""Cloud assessment portal routes (#133/#140/#152).

Surfaces the cloud assessment history + launches new checks from the portal:

    POST /api/cloud/assessments/run                  — launch a check
        body: {provider, profile?, regions?, services?, fail_on_severity?}
        runs the discovery gate (sync); if access OK, backgrounds the assessment
    GET /api/cloud/assessments                       — list (newest first)
    GET /api/cloud/assessments/{id}                  — full detail
    GET /api/cloud/assessments/{id}/download/{kind}  — download an artifact
        kind ∈ report.md | remediation.md | issues.json | remediation.pdf
    GET /api/cloud/assessment                        — newest (back-compat)

Backed by ``agents.cloud.store`` (``~/.pfactory/cloud-assessments/<id>/``).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from factory_common.logsafe import sanitize_log

# Add apps/backend to sys.path so ``import agents.cloud.store`` resolves
# (the canonical pattern used by routes/provider_runtimes.py).
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.cloud import portal_run, store  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cloud", tags=["Cloud"])


class CloudRunRequest(BaseModel):
    provider: str  # aws | azure | gcp
    profile: str | None = None  # AWS profile / GCP project / Azure subscription
    regions: list[str] = []
    services: list[str] = []
    fail_on_severity: str = "high"


async def _run_assessment_bg(req: CloudRunRequest) -> None:
    """Background: run the (slow, Docker) assessment + mirror it into the store."""
    try:
        out = await asyncio.to_thread(
            portal_run.run_and_store,
            req.provider,
            profile=req.profile,
            regions=req.regions,
            services=req.services,
            fail_on_severity=req.fail_on_severity,
        )
        logger.info(
            "cloud assessment stored: %s (%s)",
            sanitize_log(out["assessment_id"]),
            sanitize_log(out["verdict"]),
        )
    except Exception:  # never let a background failure crash the loop
        logger.exception("cloud assessment run failed for provider=%s", sanitize_log(req.provider))


@router.post("/assessments/run", summary="Launch a cloud check (gate → assessment)")
async def run_cloud_check(req: CloudRunRequest) -> dict:
    """Run the read-only access/discovery **gate**; if we get in, background the
    assessment and return immediately. The report appears under Cloud Reports."""
    if req.provider not in ("aws", "azure", "gcp"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"unsupported provider {req.provider!r}"
        )
    gate = await asyncio.to_thread(
        portal_run.preflight,
        req.provider,
        profile=req.profile,
        regions=req.regions,
        services=req.services,
    )
    if not gate["ok"]:
        # No access — do NOT proceed. The user fixes creds and retries.
        return {"gate": "no_access", "provider": req.provider, "error": gate["error"]}
    # Access confirmed → kick off the assessment in the background.
    asyncio.create_task(_run_assessment_bg(req))
    return {
        "gate": "ok",
        "provider": req.provider,
        "account": gate["account"],
        "identity": gate["identity"],
        "inventory": gate["inventory"],
        "status": "running",
    }


_DOWNLOAD_MEDIA = {
    "report.md": "text/markdown",
    "remediation.md": "text/markdown",
    "issues.json": "application/json",
    "remediation.pdf": "application/pdf",
    "report.pdf": "application/pdf",
}


@router.get("/assessments", summary="List cloud assessments (newest first)")
def list_cloud_assessments() -> dict:
    return {"assessments": store.list_assessments()}


@router.get("/assessments/{assessment_id}", summary="One cloud assessment (full)")
def get_cloud_assessment_by_id(assessment_id: str) -> dict:
    data = store.read_assessment(assessment_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assessment not found")
    return data


@router.get(
    "/assessments/{assessment_id}/download/{kind}",
    summary="Download an assessment artifact (md / json / pdf)",
)
def download_cloud_artifact(assessment_id: str, kind: str):
    if kind not in _DOWNLOAD_MEDIA:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown artifact")
    path = store.download_path(assessment_id, kind)
    if path is None or not Path(path).is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not available")
    filename = f"cloud-{assessment_id}-{kind}"
    return FileResponse(path, media_type=_DOWNLOAD_MEDIA[kind], filename=filename)


@router.get("/assessment", summary="Newest cloud assessment (back-compat)")
def get_latest_cloud_assessment() -> dict:
    items = store.list_assessments()
    if not items:
        return {"present": False}
    data = store.read_assessment(items[0]["id"])
    return data or {"present": False}
