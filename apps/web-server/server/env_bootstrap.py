"""Load ``apps/web-server/.env`` into ``os.environ`` on import.

Stdlib-only (no python-dotenv dependency). Imported first by ``main.py`` so that
``os.environ``-based config — notably ``PFACTORY_COMPLETION_WEBHOOK`` read in
``services/completion.py`` — is populated from ``.env`` without relying on the
launching shell to export it. ``setdefault`` means a real exported env var always
wins over the file. Best-effort: a missing/garbled ``.env`` is silently ignored.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load(env_path: Path | None = None) -> None:
    env_path = env_path or Path(__file__).resolve().parents[1] / ".env"
    try:
        text = env_path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load()
