"""No module may be shadowed by a same-named package beside it (#477).

When `foo.py` and `foo/` sit in the same directory, Python resolves the PACKAGE
and ignores the module. Two things follow, and both have bitten this repo:

* The module is unreachable. `project/command_registry.py` documented itself as
  a facade "so existing imports continue to work" and could never run; its body
  `from .command_registry import ...` would have imported the package that
  shadows it if it ever had.

* Static tooling cannot see past it. A whole-package mypy measurement over
  apps/backend exits 2 having checked NOTHING:

      core/workspace.py: error: Duplicate module named "core.workspace"
        (also at "core/workspace/__init__.py")
      Found 1 error in 1 file (errors prevented further checking)

  "mypy exits 2 having checked nothing" is the shape of defect that kept the
  gate dark for seven weeks. The per-file ratchet is unaffected (it checks one
  file at a time), so nothing is currently ungated — but any future sweep,
  report or whole-package measurement is blocked until the last pair is gone.

The allowlist below only ever shrinks. Adding to it is how #477 happens again.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "apps" / "backend"

# Empty, and it stays empty. `test_the_allowlist_does_not_outlive_its_entries`
# fails on any entry that is no longer shadowed, so this cannot quietly rot into
# a permanent exemption list.
_KNOWN_SHADOWED: set[str] = set()


def _shadowed_pairs() -> set[str]:
    """Every `x.py` that sits beside a package directory `x/`."""
    return {
        str(package.relative_to(_BACKEND))
        for package in _BACKEND.rglob("*")
        if package.is_dir()
        and (package / "__init__.py").is_file()
        and package.with_suffix(".py").is_file()
    }


def test_no_new_shadowed_modules():
    """A same-named module and package in one directory is never intentional."""
    unexpected = _shadowed_pairs() - _KNOWN_SHADOWED
    assert not unexpected, (
        f"module(s) shadowed by a same-named package: {sorted(unexpected)}. "
        f"Python resolves the package, so the .py file is unreachable, and a "
        f"whole-package mypy run aborts with 'Duplicate module named ...' "
        f"having checked nothing. Delete the module or fold it into the package."
    )


def test_the_allowlist_does_not_outlive_its_entries():
    """A stale allowlist entry silently re-permits the pattern it was granted for."""
    stale = _KNOWN_SHADOWED - _shadowed_pairs()
    assert not stale, (
        f"_KNOWN_SHADOWED lists {sorted(stale)}, which is no longer shadowed. "
        f"Remove the entry — an allowlist that outlives its cause is how the "
        f"next one gets waved through."
    )


def test_the_deleted_facades_stay_deleted():
    """Both were provably unreachable, and both were self-importing if reached.

    `project/command_registry.py` did `from .command_registry import ...` — the
    package that shadows it. `security.py` did `from security import *`, which
    inside apps/backend is the `security/` package, so it would have imported
    itself. Every importer in the fleet already resolves to the package.
    """
    for module, package in (
        ("project/command_registry.py", "project/command_registry"),
        ("security.py", "security"),
    ):
        assert not (_BACKEND / module).exists(), f"{module} came back"
        assert (_BACKEND / package / "__init__.py").is_file()


def test_the_packages_still_serve_the_imports_the_facades_claimed_to():
    """Deleting them must change nothing for callers — that was the whole claim."""
    import sys

    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    from project.command_registry import BASE_COMMANDS, VALIDATED_COMMANDS
    from security import bash_security_hook

    assert BASE_COMMANDS and VALIDATED_COMMANDS
    assert callable(bash_security_hook)


def test_the_merge_logic_is_reachable_by_its_real_name():
    """`core/workspace.py` was executed by PATH under the name `workspace_module`.

    That gave 55K of merge logic a second module identity no static tool could
    follow, and left it importable only through a shim. It now lives inside the
    package, so `__module__` is a name that actually resolves — which is the
    difference between "loaded" and "reachable".
    """
    import importlib
    import sys

    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    workspace = importlib.import_module("core.workspace")

    assert workspace.merge_existing_build.__module__ == "core.workspace.merge"
    assert importlib.import_module("core.workspace.merge") is sys.modules["core.workspace.merge"]
    assert "workspace_module" not in sys.modules, (
        "the importlib shim is back: the merge logic is loaded under a second "
        "identity, so anything comparing types across the two paths sees "
        "different objects"
    )
