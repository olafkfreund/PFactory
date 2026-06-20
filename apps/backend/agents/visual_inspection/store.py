"""Portal store for visual inspection runs (#170 / P2 #172).

Each run lives in its own directory under the store root
(``~/.pfactory/visual-inspections/<id>/``) so the portal can present a history
(newest-first list → detail). A run dir holds what the packager + P2 produce:
``report.md`` · ``correction-plan.md`` · ``issues.json`` · ``meta.json`` ·
``screenshots/`` · ``recording/``. Downloads (.md/.json) serve as-is; PDFs render
on demand (pandoc → headless Chrome) and cache. Mirrors ``agents/cloud/store.py``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = ["download_path", "list_runs", "read_run", "store_root", "write_run"]

_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_DOWNLOADS = {
    "report.md": "report.md",
    "correction-plan.md": "correction-plan.md",
    "issues.json": "issues.json",
    "meta.json": "meta.json",
}


def store_root() -> Path:
    override = os.environ.get("PFACTORY_VISUAL_INSPECTION_ROOT")
    return Path(override) if override else Path.home() / ".pfactory" / "visual-inspections"


def _safe_dir(run_id: str) -> Path | None:
    if not run_id or not _ID_RE.match(run_id) or run_id in {".", ".."}:
        return None
    d = store_root() / run_id
    return d if d.is_dir() else None


def write_run(run_dir: Path | str) -> Path:
    """Copy a packaged run folder into the store (keyed by its dir name). Returns
    the store path. Overwrites an existing entry with the same id."""
    src = Path(run_dir)
    dst = store_root() / src.name
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    return dst


def list_runs() -> list[dict]:
    """All stored runs, newest first, with summary metadata from meta.json."""
    root = store_root()
    if not root.is_dir():
        return []
    out: list[dict] = []
    for d in root.iterdir():
        meta = d / "meta.json"
        if not (d.is_dir() and meta.is_file()):
            continue
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "id": d.name,
                "target": m.get("target"),
                "verdict": m.get("verdict"),
                "counts": m.get("counts"),
                "created": meta.stat().st_mtime,
            }
        )
    out.sort(key=lambda r: r["created"], reverse=True)
    return out


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def read_run(run_id: str) -> dict | None:
    """Full detail for one run (meta + report + correction plan + issues)."""
    d = _safe_dir(run_id)
    if d is None:
        return None
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}
    return {
        "present": True,
        "id": run_id,
        "meta": meta,
        "reportMarkdown": _read(d / "report.md"),
        "correctionPlanMarkdown": _read(d / "correction-plan.md"),
        "issuesJson": _read(d / "issues.json"),
    }


def _render_pdf(d: Path, md_name: str) -> Path | None:
    md = d / md_name
    if not md.is_file():
        return None
    pdf = d / (md.stem + ".pdf")
    if pdf.is_file() and pdf.stat().st_mtime >= md.stat().st_mtime:
        return pdf
    pandoc = shutil.which("pandoc")
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not pandoc or not chrome:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "doc.html"
        subprocess.run(
            [pandoc, str(md), "-f", "gfm", "-t", "html", "-s", "-o", str(html)],
            capture_output=True,
            timeout=60,
        )
        if not html.is_file():
            return None
        subprocess.run(
            [
                chrome,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                f"--print-to-pdf={pdf}",
                f"file://{html}",
            ],
            capture_output=True,
            timeout=120,
        )
    return pdf if pdf.is_file() else None


def download_path(run_id: str, kind: str) -> Path | None:
    """Path to a downloadable artifact (None if absent)."""
    d = _safe_dir(run_id)
    if d is None:
        return None
    if kind in _DOWNLOADS:
        p = d / _DOWNLOADS[kind]
        return p if p.is_file() else None
    if kind == "report.pdf":
        return _render_pdf(d, "report.md")
    if kind == "correction-plan.pdf":
        return _render_pdf(d, "correction-plan.md")
    return None
