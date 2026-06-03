"""FastAPI route exposing the quote calculator — the API lane's target.

`GET /api/quote?base=&qty=&discount_pct=` returns the computed total as JSON.
Tested by the api lane (httpx against the running app) and, via the served UI,
by the browser lane (Playwright).
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from quote import quote

app = FastAPI(title="PFactory Demo — Quote API")


@app.get("/api/quote")
def get_quote(
    base: float = Query(..., ge=0, description="Per-unit base price"),
    qty: int = Query(..., ge=0, description="Number of units"),
    discount_pct: float = Query(0.0, description="Discount percent, clamped to [0,100]"),
) -> dict[str, float | int]:
    """Return ``{"base", "qty", "discount_pct", "total"}`` for the inputs."""
    try:
        total = quote(base, qty, discount_pct)
    except ValueError as exc:  # pragma: no cover - guarded by Query(ge=0)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"base": base, "qty": qty, "discount_pct": discount_pct, "total": total}
