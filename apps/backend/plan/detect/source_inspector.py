"""Extract a behavioral contract from the source impl (RFC-0010, Phase 5).

For a migration the existing code is the behavioral source of truth. This module
reads it **statically** (AST only — never executes it, per the RFC-0010 bright
line) and extracts what the rewrite must preserve: the public API surface +
signatures, the existing test files, and the module import DAG (which drives the
module-by-module rewrite order).

Python is parsed precisely via :mod:`ast`. Other source languages fall back to a
file listing (the public-surface extraction for them is a later enhancement).
"""

from __future__ import annotations

import ast
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from plan.recon._walk import is_excluded_dir, prune_dirnames

logger = logging.getLogger(__name__)


@dataclass
class PublicSymbol:
    module: str  # repo-relative module path, e.g. "pay/refund.py"
    name: str
    kind: str  # "function" | "class"
    signature: str


@dataclass
class BehavioralContract:
    language: str
    modules: list[str] = field(default_factory=list)
    public_api: list[PublicSymbol] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    module_graph: dict[str, list[str]] = field(default_factory=dict)
    # Concrete golden-corpus input vectors extracted from the existing tests
    # (literal-arg calls to the public functions). Drives the equivalence lane.
    input_vectors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "modules": self.modules,
            "public_api": [vars(s) for s in self.public_api],
            "test_files": self.test_files,
            "module_graph": self.module_graph,
            "input_vectors": self.input_vectors,
        }


def _rel(fp: Path, root: Path) -> str:
    try:
        return str(fp.resolve().relative_to(root.resolve()))
    except ValueError:
        return fp.name


def _is_test(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in f"/{rel}"


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.posonlyargs + node.args.args]
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    return f"{node.name}({', '.join(args)})"


def _py_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        prune_dirnames(dirnames)
        for name in filenames:
            if name.endswith(".py"):
                out.append(Path(dirpath) / name)
    return out


def _module_name(rel: str) -> str:
    """repo-relative .py path → dotted module (best-effort, drops __init__)."""
    mod = rel[:-3].replace("/", ".")
    return mod[: -len(".__init__")] if mod.endswith(".__init__") else mod


def build_behavioral_contract(root: Path, language: str = "python") -> BehavioralContract:
    """Statically extract the behavioral contract from ``root``.

    Never executes code. Non-Python languages get a module listing only.
    """
    root = Path(root)
    if language != "python":
        modules = sorted(
            _rel(p, root)
            for p in root.rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and not any(is_excluded_dir(part) for part in _rel(p, root).split("/")[:-1])
        )
        return BehavioralContract(language=language, modules=modules[:500])

    contract = BehavioralContract(language="python")
    files = _py_files(root)
    local_modules = {_module_name(_rel(f, root)) for f in files}
    test_trees: list[ast.Module] = []

    for fp in files:
        rel = _rel(fp, root)
        contract.modules.append(rel)
        if _is_test(rel):
            contract.test_files.append(rel)
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue

        # Public API: top-level non-underscore defs/classes. Test files are
        # recorded under test_files, not as public surface to port/capture — but
        # we keep their AST to mine concrete input vectors below.
        if _is_test(rel):
            test_trees.append(tree)
            continue
        for node in tree.body:
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not node.name.startswith("_"):
                contract.public_api.append(
                    PublicSymbol(rel, node.name, "function", _signature(node))
                )
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                contract.public_api.append(PublicSymbol(rel, node.name, "class", f"{node.name}"))

        # Module graph: edges to other local modules (intra-repo imports only).
        edges: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == m or node.module.startswith(m + ".") for m in local_modules):
                    edges.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == m or alias.name.startswith(m + ".") for m in local_modules
                    ):
                        edges.add(alias.name)
        if edges:
            contract.module_graph[_module_name(rel)] = sorted(edges)

    contract.modules.sort()
    contract.test_files.sort()
    contract.public_api.sort(key=lambda s: (s.module, s.name))
    func_module = {s.name: s.module for s in contract.public_api if s.kind == "function"}
    contract.input_vectors = _extract_vectors(test_trees, func_module)
    return contract


def _extract_vectors(test_trees: list[ast.Module], func_module: dict[str, str]) -> list[dict]:
    """Mine concrete input vectors from literal-arg calls to public functions.

    Walks the test ASTs for ``func(<literals>)`` calls to a public function and
    records the (deduped) argument tuples — the real inputs the golden corpus is
    captured over. Calls with non-literal args or keyword args are skipped (a
    captured vector must be reproducible from constants alone).
    """
    vectors: list[dict] = []
    seen: set[tuple] = set()
    counts: dict[str, int] = {}
    for tree in test_trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name not in func_module or node.keywords:
                continue
            args = []
            ok = True
            for a in node.args:
                try:
                    args.append(ast.literal_eval(a))
                except (ValueError, SyntaxError, TypeError):
                    ok = False
                    break
            if not ok:
                continue
            key = (name, repr(args))
            if key in seen:
                continue
            seen.add(key)
            counts[name] = counts.get(name, 0) + 1
            vectors.append(
                {
                    "id": f"{name}-{counts[name]}",
                    "module": func_module[name],
                    "function": name,
                    "args": args,
                }
            )
    return vectors


def inspect_source(repo: str, base_ref: str | None, language: str) -> BehavioralContract | None:
    """Clone ``repo`` read-only and extract its behavioral contract.

    Never raises — a clone/parse failure yields ``None`` and the migration plan
    proceeds without the extracted surface (the readiness gate still flags a
    modify/migration plan that couldn't read its repo).
    """
    from plan.recon.clone import clone_for_recon

    try:
        with clone_for_recon(repo, base_ref) as c:
            if not c.ok or c.path is None:
                return None
            return build_behavioral_contract(c.path, language)
    except Exception as exc:  # noqa: BLE001 - planning must never break
        logger.warning("source inspection failed for %s: %s", repo, exc)
        return None
