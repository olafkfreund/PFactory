"""Shared resolver for the gemini / antigravity CLI binary path.

Used by both the streaming and agentic Gemini providers so the resolution
logic lives in one place.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def get_gemini_binary(custom_path: str | None = None) -> str:
    """Dynamically resolve the gemini / antigravity binary path."""
    if custom_path and custom_path != "gemini":
        return custom_path
    if shutil.which("antigravity"):
        return "antigravity"
    custom_path_default = Path.home() / ".gemini" / "antigravity-cli" / "bin" / "antigravity"
    if custom_path_default.exists():
        return str(custom_path_default)
    if shutil.which("gemini"):
        return "gemini"
    # Fallback to antigravity since we preinstall it by default
    return "antigravity"
