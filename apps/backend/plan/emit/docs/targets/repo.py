"""RepoDocsTarget — the always-available sink: write the bundle to a directory.

Writes ``<root>/<slug>.md`` plus two maintained files:
- ``registry.json`` — the machine index (correlation_key -> entry), the
  cross-factory memory record;
- ``index.md`` — a human "Plans" index regenerated from the registry.

This is a pure directory writer (no git): the root is a checkout's
``techdocs/plans`` dir, or a runtime/workspace dir, or a tmp dir in tests.
Committing/PRing that dir (dry-run-first) + pushing to Backstage/Confluence are
later phases. ``publish`` never raises.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..bundle import DocBundle, TargetResult

logger = logging.getLogger(__name__)

REGISTRY_FILE = "registry.json"
INDEX_FILE = "index.md"


class RepoDocsTarget:
    name = "repo"

    def __init__(self, root: Path, *, updated_at: str = "") -> None:
        self._root = Path(root)
        self._updated_at = updated_at  # injected so the renderer stays pure

    def available(self) -> bool:
        return True  # the substrate is always available

    # ── registry helpers ────────────────────────────────────────────────

    def _registry_path(self) -> Path:
        return self._root / REGISTRY_FILE

    def _load_registry(self) -> dict[str, dict]:
        path = self._registry_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
            return data.get("plans", {}) if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 — corrupt index must not be fatal
            logger.warning("plan docs registry unreadable (%s); starting fresh", exc)
            return {}

    def _write_registry(self, plans: dict[str, dict]) -> None:
        payload = {"version": 1, "plans": plans}
        tmp = self._registry_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp.replace(self._registry_path())

    def _render_index(self, plans: dict[str, dict]) -> str:
        lines = ["# Plans\n", "\nGoverned plans emitted by PFactory.\n\n"]
        if not plans:
            lines.append("_No plans emitted yet._\n")
            return "".join(lines)
        lines.append("| Plan | Type | Epic | Key |\n")
        lines.append("|---|---|---|---|\n")
        for ck in sorted(plans):
            e = plans[ck]
            doc = e.get("doc_file", "")
            link = (
                f"[{e.get('title', e.get('plan_id'))}]({doc})"
                if doc
                else e.get("title", "")
            )
            epic = f"#{e['epic']}" if e.get("epic") else "—"
            lines.append(
                f"| {link} | {e.get('plan_type') or '—'} | {epic} | `{ck}` |\n"
            )
        return "".join(lines)

    # ── publish ─────────────────────────────────────────────────────────

    def publish(self, bundle: DocBundle) -> TargetResult:
        try:
            self._root.mkdir(parents=True, exist_ok=True)

            # 1) the plan page
            page = self._root / f"{bundle.slug}.md"
            page.write_text(bundle.markdown)

            # 2) upsert the registry (keyed by correlation_key)
            plans = self._load_registry()
            entry = dict(bundle.registry_entry)
            if self._updated_at:
                entry["updated_at"] = self._updated_at
            plans[bundle.correlation_key] = entry
            self._write_registry(plans)

            # 3) regenerate the human index
            (self._root / INDEX_FILE).write_text(self._render_index(plans))

            return TargetResult(
                target=self.name,
                status="written",
                detail={
                    "page": str(page),
                    "registry": str(self._registry_path()),
                    "plans": len(plans),
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never break emit
            logger.warning("RepoDocsTarget failed for %s: %s", bundle.plan_id, exc)
            return TargetResult(
                target=self.name, status="error", detail={"error": str(exc)}
            )
