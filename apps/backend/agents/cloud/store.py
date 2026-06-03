"""Portal store for cloud assessments (#133/#152).

Each assessment lives in its own directory under the store root
(``~/.pfactory/cloud-assessments/<id>/``), so the portal can present a **history**
(newest-first list → drill-down detail) rather than only a single "latest".

Each ``<id>/`` holds the artifacts ``assess_and_write`` produces:
``cloud_assessment.{json,md}``, ``cloud_remediation_plan.md``,
``cloud_issues.json``, ``diagrams/cloud_topology.mmd``. Downloads (.md / .json)
are served as-is; the remediation **PDF** is rendered on demand
(``pandoc`` → ``google-chrome --headless --print-to-pdf``) and cached.

Pure filesystem + subprocess; no network.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = [
    "download_path",
    "list_assessments",
    "new_assessment_id",
    "read_assessment",
    "store_root",
    "write_assessment",
]

_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Artifacts an assessment dir holds (source name under findings/ → stored name).
# diagrams/cloud_topology.mmd is handled separately (it lives in a subdir).
_ARTIFACTS = (
    "cloud_assessment.md",
    "cloud_assessment.json",
    "cloud_remediation_plan.md",
    "cloud_issues.json",
)
# download kind → filename within the assessment dir (".pdf" is rendered).
_DOWNLOADS = {
    "report.md": "cloud_assessment.md",
    "remediation.md": "cloud_remediation_plan.md",
    "issues.json": "cloud_issues.json",
}


def store_root() -> Path:
    override = os.environ.get("PFACTORY_CLOUD_ASSESSMENT_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".pfactory" / "cloud-assessments"


def _safe_dir(assessment_id: str) -> Path | None:
    """Resolve ``<root>/<id>`` if ``id`` is a safe single component + exists."""
    if not assessment_id or not _ID_RE.match(assessment_id) or assessment_id in {".", ".."}:
        return None
    d = store_root() / assessment_id
    return d if d.is_dir() else None


def _slug(value: str) -> str:
    """Collapse a value to the safe id alphabet (``[A-Za-z0-9._-]``)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-") or "x"


def new_assessment_id(provider: str, account: str | None, *, now=None) -> str:
    """A sortable, filesystem-safe id: ``<provider>-<account>-<UTC timestamp>``."""
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"{_slug(provider)}-{_slug(account or 'unknown')}-{ts}"


def write_assessment(spec_dir: Path, assessment_id: str) -> Path:
    """Copy a finished run's artifacts from ``spec_dir/findings/`` into the store.

    Mirrors the files the portal reads (report/remediation/issues JSON+MD +
    the topology diagram) into ``<root>/<assessment_id>/`` so the run shows up
    in **Cloud Reports**. Returns the created store directory.
    """
    src = Path(spec_dir) / "findings"
    dst = store_root() / _slug(assessment_id)
    dst.mkdir(parents=True, exist_ok=True)
    for name in _ARTIFACTS:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dst / name)
    diagram = src / "diagrams" / "cloud_topology.mmd"
    if diagram.is_file():
        (dst / "diagrams").mkdir(exist_ok=True)
        shutil.copy2(diagram, dst / "diagrams" / "cloud_topology.mmd")
    return dst


def list_assessments() -> list[dict]:
    """All stored assessments, newest first, with summary metadata."""
    root = store_root()
    if not root.is_dir():
        return []
    out: list[dict] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        js = d / "cloud_assessment.json"
        if not js.is_file():
            continue
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "id": d.name,
                "provider": data.get("provider"),
                "account": data.get("account"),
                "verdict": data.get("verdict"),
                "failed": data.get("failed"),
                "passed": data.get("passed"),
                "failCounts": data.get("fail_counts"),
                "created": js.stat().st_mtime,
            }
        )
    out.sort(key=lambda a: a["created"], reverse=True)
    return out


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def read_assessment(assessment_id: str) -> dict | None:
    """Full detail for one assessment (report + diagram + remediation + issues)."""
    d = _safe_dir(assessment_id)
    if d is None:
        return None
    js = d / "cloud_assessment.json"
    if not js.is_file():
        return None
    try:
        data = json.loads(js.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    return {
        "present": True,
        "id": assessment_id,
        "json": data,
        "reportMarkdown": _read(d / "cloud_assessment.md"),
        "remediationMarkdown": _read(d / "cloud_remediation_plan.md"),
        "diagramMermaid": _read(d / "diagrams" / "cloud_topology.mmd"),
        "issuesJson": _read(d / "cloud_issues.json"),
    }


def _render_pdf(d: Path, md_name: str) -> Path | None:
    """Render ``<dir>/<md_name>`` to a cached PDF via pandoc + headless Chrome."""
    md = d / md_name
    if not md.is_file():
        return None
    pdf = d / (md.stem + ".pdf")
    if pdf.is_file() and pdf.stat().st_mtime >= md.stat().st_mtime:
        return pdf  # cached + fresh
    pandoc = shutil.which("pandoc")
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not pandoc or not chrome:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "doc.html"
        subprocess.run(
            [pandoc, str(md), "-f", "gfm", "-t", "html", "-s", "-o", str(html)],
            capture_output=True, timeout=60,
        )
        if not html.is_file():
            return None
        subprocess.run(
            [chrome, "--headless", "--no-sandbox", "--disable-gpu",
             f"--print-to-pdf={pdf}", f"file://{html}"],
            capture_output=True, timeout=120,
        )
    return pdf if pdf.is_file() else None


def download_path(assessment_id: str, kind: str) -> Path | None:
    """Path to a downloadable artifact for ``assessment_id`` (None if absent)."""
    d = _safe_dir(assessment_id)
    if d is None:
        return None
    if kind in _DOWNLOADS:
        p = d / _DOWNLOADS[kind]
        return p if p.is_file() else None
    if kind == "remediation.pdf":
        return _render_pdf(d, "cloud_remediation_plan.md")
    if kind == "report.pdf":
        return _render_pdf(d, "cloud_assessment.md")
    return None
