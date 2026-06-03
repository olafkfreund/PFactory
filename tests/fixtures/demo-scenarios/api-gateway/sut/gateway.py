"""Key-Value Gateway — the API-gateway demo SUT (no browser).

A small REST service: health, list, set, get, delete over an in-memory store.
PFactory's api lane drives httpx against this running service and verifies
status codes + JSON bodies.

Seeded bug (AC#4): GET /api/keys/{key} for a MISSING key returns HTTP 200 with
``{"value": null}`` instead of HTTP 404. A contract test asserting 404 on a
missing key fails, and the Triager surfaces it as a reject.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="KV Gateway")
_STORE: dict[str, object] = {}


class SetBody(BaseModel):
    value: object


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "kv-gateway"}


@app.get("/api/keys")
def list_keys() -> dict:
    return {"keys": sorted(_STORE.keys()), "count": len(_STORE)}


@app.put("/api/keys/{key}")
def set_key(key: str, body: SetBody) -> dict:
    _STORE[key] = body.value
    return {"key": key, "value": body.value}


@app.get("/api/keys/{key}")
def get_key(key: str):
    # BUG: should 404 when the key is absent; instead returns 200 + null.
    return {"key": key, "value": _STORE.get(key)}


@app.delete("/api/keys/{key}")
def delete_key(key: str):
    existed = key in _STORE
    _STORE.pop(key, None)
    return JSONResponse({"key": key, "deleted": existed},
                        status_code=200 if existed else 404)
