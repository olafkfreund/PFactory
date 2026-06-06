"""Inbound RFC-0001 completion events from AIFactory/TFactory (epic #65, child 9).

The receiving end of the bidirectional bridge: AIFactory and TFactory POST their
completion events here; we reconcile them on the shared ``correlation_key`` and
surface which units need a handback. A process-lifetime
:class:`~plan.emit.contract_sync.ContractSyncRegistry` holds the state (mirrors
the in-memory plan ``SERVICE`` singleton).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from plan.emit.contract_sync import ContractSyncRegistry, SyncError  # noqa: E402

router = APIRouter(prefix="/api/contract-events", tags=["contract-sync"])

# Process-lifetime registry of reconciled downstream state, keyed by correlation.
REGISTRY = ContractSyncRegistry()


@router.post("")
async def ingest_event(payload: dict) -> dict:
    """Reconcile one inbound completion event; returns the updated state."""
    try:
        state = REGISTRY.apply(payload)
    except SyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return state.model_dump()


@router.get("/handbacks")
async def handbacks() -> dict:
    """All tracked units whose downstream outcome calls for a handback."""
    return {"handbacks": [s.model_dump() for s in REGISTRY.needing_handback()]}


@router.get("/{correlation_key}")
async def get_state(correlation_key: str) -> dict:
    """The reconciled state for one correlation key."""
    state = REGISTRY.get(correlation_key)
    if state is None:
        raise HTTPException(status_code=404, detail=f"no state for '{correlation_key}'")
    return state.model_dump()
