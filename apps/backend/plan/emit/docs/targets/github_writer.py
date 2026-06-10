"""Write files into a GitHub repo via the Contents API (no checkout needed).

Used by the Backstage target to land plan docs in the *target project's* repo
(the one the epic was emitted to), so Backstage renders them under that repo's
catalog entity. The ``api`` callable is injectable (default: the ``gh`` CLI), so
unit tests run with a fake and never touch the network. Honors the
no-automatic-pushes policy: writes only happen when the caller opts in.
"""

from __future__ import annotations

import base64
import json
import subprocess
from typing import Any, Callable

# api(method, path, body|None) -> parsed JSON dict
GhApi = Callable[[str, str, "dict | None"], dict]


def _default_gh_api(method: str, path: str, body: dict | None) -> dict:
    """Call ``gh api`` and parse JSON. Raises on non-zero exit."""
    args = ["gh", "api", "-X", method, path]
    if body is not None:
        args += ["--input", "-"]
    proc = subprocess.run(
        args,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh api {method} {path} failed: {proc.stderr.strip()[:300]}"
        )
    out = proc.stdout.strip()
    return json.loads(out) if out.startswith(("{", "[")) else {}


class GitHubContentsWriter:
    """Idempotent single-file upsert into ``owner/repo`` on ``branch``."""

    def __init__(
        self, repo: str, *, branch: str = "main", api: GhApi | None = None
    ) -> None:
        self.repo = repo  # "owner/repo"
        self.branch = branch
        self._api = api or _default_gh_api

    def _get(self, path: str) -> dict | None:
        try:
            res = self._api(
                "GET", f"/repos/{self.repo}/contents/{path}?ref={self.branch}", None
            )
        except Exception:  # noqa: BLE001 — 404 => file absent
            return None
        return res if isinstance(res, dict) else None

    def _get_sha(self, path: str) -> str | None:
        res = self._get(path)
        return res.get("sha") if res else None

    def get_file(self, path: str) -> str | None:
        """Return the decoded UTF-8 content of ``path``, or None if absent."""
        res = self._get(path)
        if not res or "content" not in res:
            return None
        try:
            return base64.b64decode(res["content"]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None

    def put_file(self, path: str, content: str, message: str) -> dict[str, Any]:
        """Create or update ``path`` with ``content``. Returns the API response."""
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": self.branch,
        }
        sha = self._get_sha(path)
        if sha is not None:
            body["sha"] = sha  # update existing
        return self._api("PUT", f"/repos/{self.repo}/contents/{path}", body)
