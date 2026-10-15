"""merge: tree-sitter and its Python, JavaScript and TypeScript grammars load.

``merge/semantic_analyzer.py`` fills ``LANGUAGES_AVAILABLE`` at import and
swallows every ``ImportError``, so a broken install silently drops the merge
analysis to its regex fallback. All four packages are pinned in
``apps/backend/requirements.txt``, so a missing one is an install defect.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import registry

_REQUIRED = (".py", ".js", ".ts")


def _tree_sitter_grammars_load() -> Iterator[str]:
    from merge import semantic_analyzer

    if not semantic_analyzer.TREE_SITTER_AVAILABLE:
        yield "tree-sitter failed to import: merge analysis is on its regex fallback"
        return
    for ext in _REQUIRED:
        if ext not in semantic_analyzer.LANGUAGES_AVAILABLE:
            yield f"no tree-sitter grammar for {ext}: those files fall back to regex analysis"


registry.register("merge.semantic_analyzer", _tree_sitter_grammars_load)
