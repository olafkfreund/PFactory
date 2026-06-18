"""Plan a language migration (RFC-0010, Phase 5).

Turns the source behavioral contract (from :mod:`plan.detect.source_inspector`)
into the migration-specific contract metadata the downstream factories consume:

* a **module map** (source module → target module path), ordered by the source
  import DAG so leaf modules are rewritten first;
* a **golden-corpus manifest** — which public functions to capture input/output
  vectors for, and where the inputs come from. The planner only *declares* this;
  TFactory captures the expected outputs by running the legacy source in its
  sandbox (RFC-0010 §5.2 — the planner never executes code);
* the **equivalence block** TFactory's differential lane reads.

Placement defaults to a new coexisting crate/dir (the original stays as the live
reference oracle); the caller may override after the single placement question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.detect.source_inspector import BehavioralContract

# Target language → (source-file extension, subdir convention).
_TARGET = {
    "rust": ("rs", "rust"),
    "go": ("go", "go"),
    "typescript": ("ts", "ts"),
    "javascript": ("js", "js"),
    "python": ("py", "py"),
    "java": ("java", "java"),
}

PLACEMENT_QUESTION = (
    "Place the rewrite as a new coexisting crate/dir in the same repo (default, "
    "keeps the original as a live reference oracle), or a new repo? And keep the "
    "original running during transition (strangler) or replace it?"
)


def _target_path(src_module: str, target_language: str, crate: str) -> str:
    """source module path → target module path under the new crate/dir."""
    ext, subdir = _TARGET.get(target_language, ("txt", target_language))
    stem = src_module.rsplit(".", 1)[0] if "." in src_module else src_module
    # drop a leading source dir component to keep the tree shallow, then re-root.
    return f"{subdir}/{crate}/src/{stem}.{ext}"


def _ordered_modules(contract: BehavioralContract) -> list[str]:
    """Source modules ordered leaf-first by the import DAG (deps before dependents)."""
    graph = contract.module_graph
    name_to_path = {}
    for path in contract.modules:
        mod = path[:-3].replace("/", ".") if path.endswith(".py") else path
        mod = mod[: -len(".__init__")] if mod.endswith(".__init__") else mod
        name_to_path[mod] = path

    visited: list[str] = []
    seen: set[str] = set()

    def visit(mod: str, stack: frozenset[str]) -> None:
        if mod in seen or mod in stack:
            return
        for dep in graph.get(mod, []):
            if dep in name_to_path:
                visit(dep, stack | {mod})
        seen.add(mod)
        if mod in name_to_path:
            visited.append(name_to_path[mod])

    for mod in name_to_path:
        visit(mod, frozenset())
    # append any modules not in the graph, stable order
    for path in contract.modules:
        if path not in visited:
            visited.append(path)
    return visited


def _is_portable_module(path: str) -> bool:
    """Skip non-source files: docs, test files, and package __init__ markers."""
    base = path.rsplit("/", 1)[-1]
    if path.endswith((".md", ".txt")):
        return False
    if base.startswith("test_") or base.endswith("_test.py") or "/tests/" in f"/{path}":
        return False
    return base not in ("__init__.py", "__init__.rs")


def build_module_map(
    contract: BehavioralContract, target_language: str, *, crate: str = "port"
) -> dict[str, str]:
    """source module → target module path, leaf-first (dict preserves order).

    Excludes docs, test files, and package ``__init__`` markers — only real
    source modules are ported.
    """
    return {
        src: _target_path(src, target_language, crate)
        for src in _ordered_modules(contract)
        if _is_portable_module(src)
    }


def build_golden_corpus_manifest(contract: BehavioralContract) -> dict[str, Any]:
    """Declare which public functions to capture I/O vectors for (no execution).

    Expected outputs are captured later by TFactory running the legacy source in
    its sandbox; the planner only records *what* to capture and *where* the input
    vectors come from (the existing tests).
    """
    return {
        "language": contract.language,
        "vectors_from": list(contract.test_files),
        "capture": "run-legacy-in-sandbox",
        "functions": [
            {"module": s.module, "name": s.name, "signature": s.signature}
            for s in contract.public_api
            if s.kind == "function"
        ],
    }


def build_equivalence_block(
    contract: BehavioralContract,
    target_language: str,
    *,
    crate: str = "port",
    corpus_ref: str = "findings/golden_corpus.json",
    parity_threshold: float = 1.0,
) -> dict[str, Any]:
    """The ``tfactory.equivalence`` block for the differential lane."""
    return {
        "golden_corpus_ref": corpus_ref,
        "parity_threshold": parity_threshold,
        "differential_lanes": ["equivalence"],
        "module_map": build_module_map(contract, target_language, crate=crate),
    }
