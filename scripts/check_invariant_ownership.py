#!/usr/bin/env python3
"""Every module in a covered package must declare an invariant, or say why not.

Factory#818 pilot. This is the mechanical rule the pattern depends on: without
it, a new module can be added and remain invisible to diagnostics until someone
notices the gap. A registry with 17 declarations proves nothing on its own if
the package has 18 modules.

Fails on:
  * a module in the package with no declaration    (the gap this exists to catch)
  * a declaration naming a module that no longer exists (a stale entry, which
    makes coverage look complete while watching nothing)

Run:  python3 scripts/check_invariant_ownership.py [--self-test]
"""

# Imports inside declared_modules() are deliberate: sys.path must be extended
# before the package is importable, which happens in check().
# ruff: noqa: PLC0415

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Packages under the pilot, relative to apps/backend.
COVERED = ("pfactory_secrets",)

_BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"


def modules_on_disk(pkg_root: Path, pkg: str) -> set[str]:
    """Dotted names of every .py module in the package, excluding the companion."""
    out: set[str] = set()
    for path in sorted(pkg_root.rglob("*.py")):
        rel = path.relative_to(pkg_root)
        if rel.name == "_invariants.py":
            continue
        out.add(".".join([pkg, *rel.parts[:-1], rel.stem]))
    return out


def declared_modules(pkg: str) -> set[str]:
    """Module names the package's companion actually registered."""
    import importlib

    from factory_invariants import registry

    importlib.import_module(f"{pkg}._invariants")
    return {m for m in registry.registered() if m == pkg or m.startswith(f"{pkg}.")}


def check(backend: Path = _BACKEND) -> list[str]:
    sys.path.insert(0, str(backend))
    problems: list[str] = []
    for pkg in COVERED:
        root = backend / pkg
        if not root.is_dir():
            problems.append(f"{pkg}: covered package not found at {root}")
            continue
        on_disk = modules_on_disk(root, pkg)
        declared = declared_modules(pkg)
        for missing in sorted(on_disk - declared):
            problems.append(
                f"{missing}: no invariant declaration. Add a check to "
                f"{pkg}/_invariants.py, or a written reason why this module owns "
                "no observable runtime relation."
            )
        for stale in sorted(declared - on_disk):
            problems.append(
                f"{stale}: declared in {pkg}/_invariants.py but no such module "
                "exists — coverage looks complete while watching nothing."
            )
    return problems


def _self_test() -> int:
    """Prove this checker can FAIL. A checker never seen to fail is not evidence.

    Mutates the REAL package rather than a copied fixture: copying it out of
    ``apps/backend`` orphans it from its sibling imports, so the copy fails to
    import for a reason that has nothing to do with what is being tested. A
    temporary module in the real tree is both simpler and stronger evidence.
    """
    failures: list[str] = []
    probe = _BACKEND / COVERED[0] / "_invariant_selftest_probe.py"
    if probe.exists():
        print(f"self-test FAILED: {probe} already exists", file=sys.stderr)  # noqa: T201
        return 1
    try:
        probe.write_text("# transient fixture written by --self-test\nX = 1\n")
        got = check()
        if not any("_invariant_selftest_probe" in p for p in got):
            failures.append(f"an undeclared module was NOT caught; got {got}")
        elif len(got) != 1:
            failures.append(f"expected exactly one problem, got {got}")
    finally:
        probe.unlink(missing_ok=True)

    clean = check()
    if clean:
        failures.append(f"the tree did not return to clean after the probe: {clean}")

    if failures:
        for f in failures:
            print(f"self-test FAILED: {f}", file=sys.stderr)  # noqa: T201
        return 1
    print("self-test OK: an undeclared module is caught, and the tree returns to clean")  # noqa: T201
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="prove the checker can fail")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    problems = check()
    if problems:
        print("INVARIANT OWNERSHIP GAP:")  # noqa: T201
        for p in problems:
            print(f"  - {p}")  # noqa: T201
        return 1
    for pkg in COVERED:
        print(f"OK: every module in {pkg} declares an invariant or says why not.")  # noqa: T201
    return 0


if __name__ == "__main__":
    os.environ.setdefault("FACTORY_INVARIANTS", "0")
    sys.exit(main())
