#!/usr/bin/env python3
"""Every module in a covered package must declare an invariant, or say why not.

Factory#818. This is the mechanical rule the pattern depends on: without it, a
new module can be added and remain invisible to diagnostics until someone
notices the gap. A registry with 17 declarations proves nothing on its own if
the package has 18 modules.

Fails on:
  * a module in the package with no declaration    (the gap this exists to catch)
  * a declaration naming a module that no longer exists (a stale entry, which
    makes coverage look complete while watching nothing)

A covered package names its own source root and its own companion module,
because the companion does not always live inside the package it watches:
``factory_common`` is vendored byte-identically from the hub and its module set
is derived by the drift gate from the canonical directory, so a companion added
inside it would be rejected as an unexpected module. It sits beside the package
instead. Being able to watch a package you do not own the source of is the point
of carrying the companion name here rather than assuming ``<pkg>._invariants``.

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
from typing import NamedTuple


class Covered(NamedTuple):
    """One package under the ownership rule."""

    #: Source root the package is importable from, relative to the repo root.
    root: str
    #: Top-level package name.
    package: str
    #: Importable module that performs the registrations for this package.
    companion: str


COVERED: tuple[Covered, ...] = (
    Covered("apps/backend", "pfactory_secrets", "pfactory_secrets._invariants"),
    # The registry itself. It was the one package the checker did not look at,
    # which is this whole failure mode in miniature.
    Covered("apps/backend", "factory_invariants", "factory_invariants._invariants"),
    # Vendored from the hub, so its companion is a sibling FILE, not a member.
    Covered("apps/web-server", "factory_common", "factory_common_invariants"),
)

_REPO = Path(__file__).resolve().parents[1]


def modules_on_disk(pkg_root: Path, pkg: str) -> set[str]:
    """Dotted names of every .py module in the package, excluding the companion."""
    out: set[str] = set()
    for path in sorted(pkg_root.rglob("*.py")):
        rel = path.relative_to(pkg_root)
        if rel.name == "_invariants.py":
            continue
        out.add(".".join([pkg, *rel.parts[:-1], rel.stem]))
    return out


def declared_modules(entry: Covered) -> set[str]:
    """Module names this package's companion actually registered.

    Both imports go through importlib rather than an import statement. That is
    what the code genuinely does -- no covered package is importable until
    check() has put its source root on sys.path -- and it is also what lets this
    file type-check: the ratchet runs mypy per package root, so from ``scripts``
    a static ``from factory_invariants import ...`` resolves to nothing and is
    unfixable from inside this file.
    """
    import importlib

    registry = importlib.import_module("factory_invariants").registry
    importlib.import_module(entry.companion)
    registered: set[str] = registry.registered()
    pkg = entry.package
    return {m for m in registered if m == pkg or m.startswith(f"{pkg}.")}


def check(repo: Path = _REPO) -> list[str]:
    problems: list[str] = []
    for entry in COVERED:
        root = repo / entry.root
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        pkg_dir = root / entry.package
        if not pkg_dir.is_dir():
            problems.append(f"{entry.package}: covered package not found at {pkg_dir}")
            continue
        on_disk = modules_on_disk(pkg_dir, entry.package)
        declared = declared_modules(entry)
        for missing in sorted(on_disk - declared):
            problems.append(
                f"{missing}: no invariant declaration. Add a check to "
                f"{entry.companion}, or a written reason why this module owns "
                "no observable runtime relation."
            )
        for stale in sorted(declared - on_disk):
            problems.append(
                f"{stale}: declared in {entry.companion} but no such module "
                "exists — coverage looks complete while watching nothing."
            )
    return problems


def _self_test() -> int:
    """Prove this checker can FAIL, once per covered package.

    Per package, not once: each entry carries its own source root and its own
    companion, so a wiring mistake in one of them (a companion that is never
    imported, a root that is never put on sys.path) would leave that package
    silently unwatched while the others kept the check green. A single probe in
    the first package cannot see that.

    Mutates the REAL packages rather than copied fixtures: copying one out of
    its source root orphans it from its sibling imports, so the copy fails to
    import for a reason that has nothing to do with what is being tested. A
    temporary module in the real tree is both simpler and stronger evidence.
    """
    failures: list[str] = []
    for entry in COVERED:
        probe = _REPO / entry.root / entry.package / "_invariant_selftest_probe.py"
        if probe.exists():
            print(f"self-test FAILED: {probe} already exists", file=sys.stderr)  # noqa: T201
            return 1
        try:
            probe.write_text("# transient fixture written by --self-test\nX = 1\n")
            got = check()
            if not any("_invariant_selftest_probe" in p for p in got):
                failures.append(f"{entry.package}: an undeclared module was NOT caught; got {got}")
            elif len(got) != 1:
                failures.append(f"{entry.package}: expected exactly one problem, got {got}")
        finally:
            probe.unlink(missing_ok=True)

    clean = check()
    if clean:
        failures.append(f"the tree did not return to clean after the probes: {clean}")

    if failures:
        for f in failures:
            print(f"self-test FAILED: {f}", file=sys.stderr)  # noqa: T201
        return 1
    covered = ", ".join(e.package for e in COVERED)
    print(  # noqa: T201
        f"self-test OK: an undeclared module is caught in each of {covered}, "
        "and the tree returns to clean"
    )
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
    for entry in COVERED:
        print(  # noqa: T201
            f"OK: every module in {entry.package} declares an invariant or says why not."
        )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("FACTORY_INVARIANTS", "0")
    sys.exit(main())
