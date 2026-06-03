"""Git-markdown knowledge connector — search a repo's docs directory (#11).

Scans Markdown files (``**/*.md``) under a docs root and ranks them by keyword
overlap against the plan query. Prioritises ``docs/``, ADRs (``docs/adr/*`` or
paths containing ``adr``) and RFCs. Pure standard library (``pathlib`` + ``re``)
so it is fully unit-testable against a temporary directory and never touches the
network.

Read-only: the connector only reads and searches files; it never writes back.
"""

from __future__ import annotations

import re
from pathlib import Path

from plan.enrich.knowledge.base import (
    KnowledgeConnector,
    KnowledgeKind,
    KnowledgeRef,
    register_connector,
)

# Caps to keep scanning bounded and skip huge/binary blobs.
_MAX_FILES = 2000
_MAX_BYTES = 512 * 1024  # 512 KiB per file
_SNIPPET_WINDOW = 200
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _tokenize(text: str) -> list[str]:
    """Lowercased alphanumeric word tokens."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _first_h1(text: str) -> str:
    """Return the first Markdown H1 (``# Title``), or '' if none."""
    m = _H1_RE.search(text)
    return m.group(1).strip() if m else ""


def _looks_like_adr(rel_path: str, title: str) -> bool:
    """True if the path/title resembles an Architecture Decision Record."""
    low = f"{rel_path} {title}".lower()
    return "adr" in low or bool(re.search(r"\brfc\b", low))


def _infer_kind(rel_path: str, title: str) -> KnowledgeKind:
    """Infer the KnowledgeRef kind from the path and title."""
    low = f"{rel_path} {title}".lower()
    if _looks_like_adr(rel_path, title):
        return "adr"
    if "policy" in low:
        return "policy"
    return "doc"


def _best_snippet(body: str, query_terms: set[str]) -> str:
    """Return the ~200-char window of ``body`` with the most query-term hits."""
    if not body:
        return ""
    lowered = body.lower()
    # Find positions of any query term; centre a window on the densest cluster.
    positions = [
        m.start()
        for m in _WORD_RE.finditer(lowered)
        if m.group(0) in query_terms
    ]
    if not positions:
        return body[:_SNIPPET_WINDOW].strip()
    half = _SNIPPET_WINDOW // 2
    best_start = 0
    best_hits = -1
    for pos in positions:
        start = max(0, pos - half)
        end = start + _SNIPPET_WINDOW
        hits = sum(1 for p in positions if start <= p < end)
        if hits > best_hits:
            best_hits = hits
            best_start = start
    snippet = body[best_start : best_start + _SNIPPET_WINDOW].strip()
    return snippet


@register_connector
class GitMarkdownConnector(KnowledgeConnector):
    """Search Markdown docs in a Git repo's docs directory (read-only)."""

    name = "git-markdown"

    def __init__(self, *, root: str | Path | None = None, **opts: object) -> None:
        """Create the connector.

        Args:
            root: Docs directory to scan. Defaults to the current working dir.
            **opts: Forwarded to the base connector.
        """
        super().__init__(**opts)
        self.root = Path(root) if root is not None else Path.cwd()

    def available(self) -> bool:
        """True if the configured docs root exists and is a directory."""
        try:
            return self.root.is_dir()
        except OSError:  # pragma: no cover - defensive
            return False

    def _iter_markdown(self) -> list[Path]:
        """Return Markdown files under root, docs/ADR-prioritised, capped."""

        def priority(path: Path) -> tuple[int, str]:
            rel = path.relative_to(self.root).as_posix().lower()
            if _looks_like_adr(rel, ""):
                rank = 0
            elif rel.startswith("docs/") or "/docs/" in rel:
                rank = 1
            else:
                rank = 2
            return (rank, rel)

        files = sorted(self.root.rglob("*.md"), key=priority)
        return files[:_MAX_FILES]

    def _read(self, path: Path) -> str | None:
        """Read a small text file; skip huge/binary ones."""
        try:
            if path.stat().st_size > _MAX_BYTES:
                return None
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def search(self, query: str, *, limit: int = 10) -> list[KnowledgeRef]:
        """Rank Markdown docs by keyword overlap with ``query``."""
        query_terms = set(_tokenize(query))
        if not query_terms:
            return []

        hits: list[KnowledgeRef] = []
        for path in self._iter_markdown():
            text = self._read(path)
            if text is None:
                continue
            rel = path.relative_to(self.root).as_posix()
            title = _first_h1(text) or path.stem
            doc_terms = set(_tokenize(title)) | set(_tokenize(text))
            overlap = query_terms & doc_terms
            if not overlap:
                continue
            score = len(overlap) / len(query_terms)
            hits.append(
                KnowledgeRef(
                    connector=self.name,
                    kind=_infer_kind(rel, title),
                    title=title,
                    uri=rel,
                    snippet=_best_snippet(text, query_terms),
                    score=round(min(1.0, score), 4),
                    metadata={"path": rel},
                )
            )

        hits.sort(key=lambda ref: (-ref.score, ref.uri))
        return hits[:limit]

    def fetch(self, uri: str) -> str:
        """Return the full text of a previously returned ref's ``uri``."""
        path = (self.root / uri).resolve()
        return path.read_text(encoding="utf-8")
