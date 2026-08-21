"""Every name the build/followup CLI imports must actually be importable.

PFactory#607: `cli/build_commands.py` imported `run_autonomous_agent` from
`agent`, and no such name exists anywhere in the tree -- the fork removed the
coder agent and never pruned the CLI. The import sat as the FIRST statement of
`handle_build_command`, so every build the cockpit started died there:

    ImportError: cannot import name 'run_autonomous_agent' from 'agent'

reproduced against the deployed pod, not just a checkout.

It stayed invisible because nothing asserted that a lazily-imported name
resolves. A module-level `import` would have been caught by any test that
imports the module; a function-local one is only caught by running the
function, and the only test that did (`tests/test_refactoring.py`) sits outside
every path CI collects (Factory#844).

So this asserts the property directly -- the names, not the behaviour -- which
is cheap, needs no Claude SDK client, and fails the moment a lazy import names
something that is not there.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"


def _lazy_imports(module_path: Path) -> list[tuple[str, str]]:
    """(module, name) for every `from X import Y` inside a function body."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    if alias.name != "*":
                        out.append((node.module, alias.name))
    return out


@pytest.mark.parametrize("filename", ["build_commands.py", "followup_commands.py"])
def test_every_lazy_import_in_the_build_cli_resolves(filename: str) -> None:
    path = _BACKEND / "cli" / filename
    assert path.exists(), f"{filename} moved; update this test rather than deleting it"

    pairs = _lazy_imports(path)
    # Guard the measurement itself: a refactor that removes every lazy import
    # would make this test vacuously green (Factory#832).
    assert pairs, f"no lazy imports found in {filename} -- this test examined nothing"

    unresolved: list[str] = []
    for module_name, symbol in pairs:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            # A missing third-party dependency is an environment problem, not
            # the defect under test; a missing FIRST-PARTY module is the defect.
            if module_name.split(".")[0] in {"agent", "agents", "core", "cli", "debug"}:
                unresolved.append(f"{module_name} ({exc})")
            continue
        if not hasattr(module, symbol):
            unresolved.append(f"{module_name}.{symbol}")

    assert not unresolved, (
        f"{filename} lazily imports names that do not exist: {unresolved}. "
        "A lazy import only fails when the function runs, so this reaches "
        "users as a broken command rather than a broken build (PFactory#607)."
    )
