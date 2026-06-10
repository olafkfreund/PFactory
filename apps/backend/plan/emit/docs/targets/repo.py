"""RepoDocsTarget — the always-available sink: write the bundle to a directory.

Writes ``<root>/<slug>.md`` plus two maintained files: ``registry.json`` (the
machine memory index) and ``index.md`` (the human index). A pure local-directory
writer (no git): the root is a checkout's ``techdocs/plans`` dir, a runtime dir,
or a tmp dir in tests. ``publish`` never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..bundle import DocBundle, TargetResult
from . import registry as reg

logger = logging.getLogger(__name__)


class RepoDocsTarget:
    name = "repo"

    def __init__(self, root: Path, *, updated_at: str = "") -> None:
        self._root = Path(root)
        self._updated_at = updated_at  # injected so the renderer stays pure

    def available(self) -> bool:
        return True  # the substrate is always available

    def publish(self, bundle: DocBundle) -> TargetResult:
        try:
            self._root.mkdir(parents=True, exist_ok=True)

            # 1) the plan page
            page = self._root / f"{bundle.slug}.md"
            page.write_text(bundle.markdown)

            # 2) upsert the registry (keyed by correlation_key)
            reg_path = self._root / reg.REGISTRY_FILE
            existing = reg.parse_registry(
                reg_path.read_text() if reg_path.exists() else None
            )
            plans = reg.upsert(
                existing, bundle.registry_entry, updated_at=self._updated_at
            )
            tmp = reg_path.with_suffix(".json.tmp")
            tmp.write_text(reg.dump_registry(plans))
            tmp.replace(reg_path)

            # 3) regenerate the human index
            (self._root / reg.INDEX_FILE).write_text(reg.render_index(plans))

            return TargetResult(
                target=self.name,
                status="written",
                detail={
                    "page": str(page),
                    "registry": str(reg_path),
                    "plans": len(plans),
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never break emit
            logger.warning("RepoDocsTarget failed for %s: %s", bundle.plan_id, exc)
            return TargetResult(
                target=self.name, status="error", detail={"error": str(exc)}
            )
