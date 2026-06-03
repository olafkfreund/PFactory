"""Provider runtime version routes (#121 phase 2).

Surface the backend ``provider_runtime`` manager in the portal so operators can
see installed-vs-latest for each provider CLI/SDK (Claude · Codex · Copilot ·
Gemini/Antigravity · Ollama), update to latest or a specific version, and pin a
known-good version to roll back when an upstream release breaks something.

    GET  /api/provider-runtimes                  — status for every runtime
    POST /api/provider-runtimes/{name}/pin       — pin/clear a version
    POST /api/provider-runtimes/{name}/update    — install/update (live)

Installs are explicit (POST only), never silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

# Add apps/backend to sys.path so ``import provider_runtime`` resolves (the
# canonical pattern used by routes/mcp.py + services/auto_fix_service.py).
_BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import provider_runtime as pr  # noqa: E402  (after sys.path insert)

router = APIRouter(prefix="/api/provider-runtimes", tags=["Provider Runtimes"])


class _VersionBody(BaseModel):
    version: str | None = None  # None = latest (update) / clear pin (pin)


def _status_dict(s: pr.RuntimeStatus) -> dict:
    return {
        "name": s.name,
        "kind": s.kind,
        "managed": s.managed,
        "installed": s.installed,
        "installedVersion": s.installed_version,
        "latestVersion": s.latest_version,
        "pinnedVersion": s.pinned_version,
        "updateAvailable": s.update_available,
    }


@router.get("", summary="Status of every provider runtime")
def list_provider_runtimes(check_latest: bool = True) -> dict:
    # check_latest=false skips the network calls (npm view / PyPI) for a fast
    # detect-only response.
    return {
        "runtimes": [
            _status_dict(s) for s in pr.get_all_status(check_latest=check_latest)
        ]
    }


@router.post("/{name}/pin", summary="Pin (or clear) a provider runtime version")
def pin_provider_runtime(name: str, body: _VersionBody) -> dict:
    try:
        pr.set_pin(name, body.version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _status_dict(pr.get_status(name, check_latest=False))


@router.post("/{name}/update", summary="Install/update a provider runtime (live)")
def update_provider_runtime(name: str, body: _VersionBody) -> dict:
    try:
        result = pr.run_install(name, body.version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {
        "name": result.name,
        "command": result.command,
        "returncode": result.returncode,
        "output": result.output,
        "installedVersion": result.installed_version,
        "ok": result.returncode == 0,
    }
