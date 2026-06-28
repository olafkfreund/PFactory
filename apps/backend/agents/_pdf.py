"""Shared Markdown -> PDF rendering for agent report stores.

Renders ``<dir>/<md_name>`` to a cached sibling PDF via pandoc + headless
Chrome. Dependency-free (stdlib only) so any agent store can import it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def render_pdf(d: Path, md_name: str) -> Path | None:
    """Render ``<dir>/<md_name>`` to a cached PDF via pandoc + headless Chrome.

    Returns the PDF path, the cached one if it is newer than the source, or
    ``None`` when the source is missing or the toolchain is unavailable.
    """
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
        # Executables come from shutil.which() and every other arg is a path we
        # built; no shell, no untrusted input.
        subprocess.run(  # noqa: S603
            [pandoc, str(md), "-f", "gfm", "-t", "html", "-s", "-o", str(html)],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if not html.is_file():
            return None
        subprocess.run(  # noqa: S603
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
            check=False,
        )
    return pdf if pdf.is_file() else None
