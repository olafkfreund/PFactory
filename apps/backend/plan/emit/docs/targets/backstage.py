"""BackstageTarget — land plan docs in the target repo + trigger Backstage.

Backstage renders TechDocs inline from the repo (builder: 'local'), so this
target (a) writes the plan page + registry + index into the *target project's*
repo under ``techdocs/plans/`` via the Contents API, then (b) nudges Backstage
to discover/rebuild (``catalog/refresh`` + ``techdocs/sync``).

- Writes happen only when ``git_write`` is opted in (no automatic pushes).
- The HTTP sync calls + the GitHub writer are injectable → unit-tested with
  fakes, no network. ``publish`` never raises.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from ..bundle import DocBundle, TargetResult
from . import registry as reg
from .github_writer import GitHubContentsWriter

logger = logging.getLogger(__name__)

# http(method, url) -> status_code
HttpFn = Callable[[str, str], int]


def _default_http(method: str, url: str) -> int:
    import httpx

    with httpx.Client(timeout=20.0, follow_redirects=True) as c:
        return c.request(method, url).status_code


class BackstageTarget:
    name = "backstage"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        repo: str | None = None,
        branch: str = "main",
        component: str = "pfactory",
        docs_subdir: str = "techdocs/plans",
        git_write: bool = False,
        writer: GitHubContentsWriter | None = None,
        http: HttpFn | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("BACKSTAGE_BASE_URL", "")).rstrip(
            "/"
        )
        self._repo = repo
        self._branch = branch
        self._component = component
        self._subdir = docs_subdir.strip("/")
        self._git_write = git_write
        self._writer = writer
        self._http = http or _default_http

    def available(self) -> bool:
        return bool(self._base_url)

    # ── sync ────────────────────────────────────────────────────────────

    def _sync(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            out["refresh"] = self._http("POST", f"{self._base_url}/api/catalog/refresh")
        except Exception as exc:  # noqa: BLE001
            out["refresh_error"] = str(exc)
        try:
            out["techdocs_sync"] = self._http(
                "GET",
                f"{self._base_url}/api/techdocs/sync/default/component/{self._component}",
            )
        except Exception as exc:  # noqa: BLE001
            out["techdocs_sync_error"] = str(exc)
        return out

    # ── publish ─────────────────────────────────────────────────────────

    def publish(self, bundle: DocBundle) -> TargetResult:
        try:
            detail: dict[str, Any] = {"base_url": self._base_url, "repo": self._repo}

            if self._git_write and self._repo:
                writer = self._writer or GitHubContentsWriter(
                    self._repo, branch=self._branch
                )
                base = self._subdir
                # page
                writer.put_file(
                    f"{base}/{bundle.slug}.md",
                    bundle.markdown,
                    f"docs(plan): {bundle.plan_id}",
                )
                # registry round-trip (read existing → upsert → write)
                existing = reg.parse_registry(
                    writer.get_file(f"{base}/{reg.REGISTRY_FILE}")
                )
                plans = reg.upsert(existing, bundle.registry_entry)
                writer.put_file(
                    f"{base}/{reg.REGISTRY_FILE}",
                    reg.dump_registry(plans),
                    f"docs(plan): registry {bundle.plan_id}",
                )
                writer.put_file(
                    f"{base}/{reg.INDEX_FILE}",
                    reg.render_index(plans),
                    f"docs(plan): index {bundle.plan_id}",
                )
                detail["wrote"] = [
                    f"{base}/{bundle.slug}.md",
                    f"{base}/{reg.REGISTRY_FILE}",
                ]
            else:
                detail["dry_run"] = True  # no git write opt-in (or no repo)

            detail["sync"] = self._sync()
            return TargetResult(target=self.name, status="written", detail=detail)
        except Exception as exc:  # noqa: BLE001 — best-effort, never break emit
            logger.warning("BackstageTarget failed for %s: %s", bundle.plan_id, exc)
            return TargetResult(
                target=self.name, status="error", detail={"error": str(exc)}
            )
