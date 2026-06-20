"""Shared registry + index helpers used by the repo and Backstage targets.

The registry (``registry.json``) is the machine memory index keyed by
``correlation_key``; the index (``index.md``) is the human "Plans" page derived
from it. Pure functions so both targets (local dir, GitHub Contents API) stay
thin.
"""

from __future__ import annotations

import json
from typing import Any

REGISTRY_FILE = "registry.json"
INDEX_FILE = "index.md"


def parse_registry(text: str | None) -> dict[str, dict]:
    """Parse a registry.json blob into ``{correlation_key: entry}`` (lenient)."""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001 — corrupt index must not be fatal
        return {}
    return data.get("plans", {}) if isinstance(data, dict) else {}


def dump_registry(plans: dict[str, dict]) -> str:
    """Serialize the registry deterministically (stable for idempotent writes)."""
    return json.dumps({"version": 1, "plans": plans}, indent=2, sort_keys=True) + "\n"


def upsert(
    plans: dict[str, dict], entry: dict[str, Any], *, updated_at: str = ""
) -> dict[str, dict]:
    """Return a new registry with ``entry`` upserted by its correlation_key."""
    out = dict(plans)
    e = dict(entry)
    if updated_at:
        e["updated_at"] = updated_at
    out[e["correlation_key"]] = e
    return out


def render_index(plans: dict[str, dict]) -> str:
    """Render the human Plans index from the registry."""
    lines = ["# Plans\n", "\nGoverned plans emitted by PFactory.\n\n"]
    if not plans:
        lines.append("_No plans emitted yet._\n")
        return "".join(lines)
    lines.append("| Plan | Type | Epic | Key |\n")
    lines.append("|---|---|---|---|\n")
    for ck in sorted(plans):
        e = plans[ck]
        doc = e.get("doc_file", "")
        title = e.get("title", e.get("plan_id", ck))
        link = f"[{title}]({doc})" if doc else title
        epic = f"#{e['epic']}" if e.get("epic") else "—"
        lines.append(f"| {link} | {e.get('plan_type') or '—'} | {epic} | `{ck}` |\n")
    return "".join(lines)
