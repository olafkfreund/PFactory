"""The plan/recon + plan/detect walkers skip venv/vendored/cache dirs (#278)."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.detect.source_inspector import build_behavioral_contract  # noqa: E402
from plan.recon._walk import is_excluded_dir, iter_files  # noqa: E402


def _tree_with_venv(tmp_path: Path) -> Path:
    """A tiny app plus nested venv/vendored/cache dirs that must be ignored."""
    (tmp_path / "app.py").write_text("def run(x):\n    return x\n")
    for d in (
        "venv/lib/python3.13/site-packages/pip",
        ".venv/lib",
        "node_modules/pkg",
        "vendor/lib",
        "__pycache__",
        ".aifactory/worktrees/w1/venv",
        "dist",
    ):
        sub = tmp_path / d
        sub.mkdir(parents=True)
        (sub / "noise.py").write_text("def vendored():\n    pass\n")
        (sub / "noise.tf").write_text("# vendored\n")
    return tmp_path


def test_is_excluded_dir() -> None:
    for name in ("venv", "venv312", ".venv", ".venv-old", "node_modules", "__pycache__"):
        assert is_excluded_dir(name)
    # .github must stay walkable (ci_probe reads workflow files); src is code.
    for name in (".github", "src", "tests"):
        assert not is_excluded_dir(name)


def test_behavioral_contract_skips_nested_venv(tmp_path: Path) -> None:
    root = _tree_with_venv(tmp_path)
    c = build_behavioral_contract(root)
    assert c.modules == ["app.py"]
    assert [s.name for s in c.public_api] == ["run"]


def test_non_python_module_listing_skips_nested_venv(tmp_path: Path) -> None:
    root = _tree_with_venv(tmp_path)
    (root / "main.go").write_text("package main\n")
    c = build_behavioral_contract(root, language="go")
    assert c.modules == ["app.py", "main.go"]


def test_iter_files_skips_nested_venv(tmp_path: Path) -> None:
    root = _tree_with_venv(tmp_path)
    (root / "infra.tf").write_text("# real\n")
    found = iter_files(root, (".tf",), 1000)
    assert [f.name for f in found] == ["infra.tf"]
