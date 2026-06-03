"""Portal-launched cloud check: discovery gate → assessment → store (#133).

The portal's "Cloud Infrastructure" +Task template calls this. It models the
user's intended flow: *first* check access + see what's there (the discovery
gate), and only if we got in run the heavier Prowler assessment and file the
report so it shows up in **Cloud Reports**.

Two seams, both injectable so the web layer + tests don't touch a real cloud:

* :func:`preflight` — read-only access + inventory (fast, host CLIs). This is
  the gate: "do we get in, and what's here?"
* :func:`run_and_store` — full assessment (Docker Prowler, minutes) + mirror the
  artifacts into the portal store. Run this in a background thread.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from . import store
from .discovery import discover
from .runner import run_cloud_assessment

__all__ = ["preflight", "run_and_store"]


def preflight(
    provider: str,
    *,
    profile: str | None = None,
    regions: list[str] | None = None,
    services: list[str] | None = None,
    discover_fn: Callable | None = None,
) -> dict:
    """The access/discovery gate — read-only identity + inventory.

    Returns ``{ok, provider, account, identity, inventory, error}``. ``ok`` is
    False (with ``error`` set) when credentials don't grant access — the caller
    must NOT proceed to the assessment in that case.
    """
    inv = (discover_fn or discover)(
        provider, profile=profile, regions=regions, services=services
    )
    err = inv.get("error")
    return {
        "ok": not err,
        "provider": provider,
        "account": inv.get("account"),
        "identity": inv.get("identity"),
        "inventory": inv,
        "error": err,
    }


def _account_from(spec_dir: Path, fallback: str | None) -> str | None:
    """Prefer the account the report recorded; fall back to the preflight one."""
    rpt = Path(spec_dir) / "findings" / "cloud_assessment.json"
    try:
        return json.loads(rpt.read_text(encoding="utf-8")).get("account") or fallback
    except (OSError, json.JSONDecodeError, ValueError):
        return fallback


def run_and_store(
    provider: str,
    *,
    profile: str | None = None,
    regions: list[str] | None = None,
    services: list[str] | None = None,
    fail_on_severity: str = "high",
    account: str | None = None,
    run_fn: Callable | None = None,
    store_mod=store,
) -> dict:
    """Run the assessment end-to-end and mirror the report into the store.

    Returns ``{assessment_id, verdict, fail_counts}``. Blocking (the Prowler
    container runs for minutes) — call from a background thread.
    """
    spec_dir = Path(tempfile.mkdtemp(prefix="pfactory-cloudrun-"))
    target = SimpleNamespace(
        provider=provider,
        profile=profile,
        regions=list(regions or []),
        scan=SimpleNamespace(
            services=list(services or []), fail_on_severity=fail_on_severity
        ),
    )
    result = (run_fn or run_cloud_assessment)(spec_dir, target)
    acct = _account_from(spec_dir, account)
    aid = store_mod.new_assessment_id(provider, acct)
    store_mod.write_assessment(spec_dir, aid)
    return {
        "assessment_id": aid,
        "verdict": result.get("verdict"),
        "fail_counts": result.get("fail_counts"),
    }
