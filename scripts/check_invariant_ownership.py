#!/usr/bin/env python3
"""Every top-level package must declare an invariant, or say why not.

Factory#818 / RFC-0024 section 3. The coverage unit is the package: a directory
with an ``__init__.py`` directly under a source root. The checker discovers them,
so a new package cannot stay invisible to diagnostics because nobody listed it.

Each package owns a companion ``<pkg>/_invariants.py`` that registers a real
check or a written reason. ``factory_common`` is vendored byte-for-byte and its
drift gate rejects extra files, so its companion is the sibling file named in
``VENDORED``.

Fails on:
  * a package with no declaration
  * a registered name whose package or module no longer exists (a stale entry)
  * a source root that is missing or has no packages
  * a ``VENDORED`` entry that does not exist on disk

Run:  python3 scripts/check_invariant_ownership.py [--self-test]
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE_ROOTS: tuple[str, ...] = ("apps/backend", "apps/web-server")
#: Vendored package -> sibling companion module (the package itself is not ours to edit).
VENDORED: dict[str, str] = {"factory_common": "factory_common_invariants"}

_REPO = Path(__file__).resolve().parents[1]
_PROBE = "_invariant_selftest_probe"


def discover(repo: Path, roots: tuple[str, ...] | list[str]) -> tuple[dict[str, str], list[str]]:
    """Map every top-level package to its source root; report empty or missing roots."""
    found: dict[str, str] = {}
    problems: list[str] = []
    for root in roots:
        pkgs = sorted(p.parent.name for p in (repo / root).glob("*/__init__.py"))
        if not pkgs:
            problems.append(f"{root}: source root is missing or contains no packages")
        for name in pkgs:
            if name in found:
                problems.append(f"{name}: package exists in both {found[name]} and {root}")
            found[name] = root
    return found, problems


def _resolves(root: Path, name: str) -> bool:
    """True if the dotted name is a module or package under the root."""
    base = root.joinpath(*name.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def check(repo: Path = _REPO) -> list[str]:
    # importlib, not import statements: nothing is importable until the roots are
    # on sys.path, and mypy (run per package root) cannot resolve it from scripts.
    for root in SOURCE_ROOTS:
        if str(repo / root) not in sys.path:
            sys.path.insert(0, str(repo / root))
    packages, problems = discover(repo, SOURCE_ROOTS)
    for pkg, companion in VENDORED.items():
        vroot = repo / packages.get(pkg, SOURCE_ROOTS[-1])
        if pkg not in packages or not (vroot / f"{companion}.py").is_file():
            problems.append(f"{pkg}: VENDORED entry needs package and {companion}.py in {vroot}")
    registry = importlib.import_module("factory_invariants").registry
    for pkg, root_name in packages.items():
        base = repo / root_name
        companion = VENDORED.get(pkg, f"{pkg}._invariants")
        path = base / f"{companion}.py" if pkg in VENDORED else base / pkg / "_invariants.py"
        if not path.is_file():
            problems.append(
                f"{pkg}: no declaration. Add {path.relative_to(repo)} registering a check "
                "or a written reason why this package owns no observable runtime relation."
            )
            continue
        importlib.import_module(companion)
    registered: set[str] = registry.registered()
    for pkg in packages:
        own = repo / packages[pkg] / pkg / "_invariants.py"
        owns_companion = pkg in VENDORED or own.is_file()
        if owns_companion and not any(n == pkg or n.startswith(f"{pkg}.") for n in registered):
            problems.append(f"{pkg}: companion registered nothing")
    for name in sorted(registered):
        top = name.split(".")[0]
        if top not in packages or not _resolves(repo / packages[top], name):
            problems.append(
                f"{name}: registered but no such package or module exists - "
                "coverage looks complete while watching nothing."
            )
    return problems


def _self_test() -> int:
    """Prove this checker can FAIL, once per source root."""
    failures: list[str] = []
    for root in SOURCE_ROOTS:
        probe = _REPO / root / _PROBE
        if probe.exists():
            print(f"self-test FAILED: {probe} already exists", file=sys.stderr)  # noqa: T201
            return 1
        try:
            probe.mkdir()
            (probe / "__init__.py").write_text("# transient fixture written by --self-test\n")
            got = check()
            if len(got) != 1 or _PROBE not in got[0]:
                failures.append(f"{root}: expected exactly one problem naming the probe, got {got}")
        finally:
            shutil.rmtree(probe, ignore_errors=True)

    clean = check()
    if clean:
        failures.append(f"the tree did not return to clean after the probes: {clean}")

    with tempfile.TemporaryDirectory() as tmp:
        if not discover(Path(tmp), ["."])[1]:
            failures.append("an empty source root was NOT reported")

    registry = importlib.import_module("factory_invariants").registry
    registry.register(
        "factory_invariants._invariant_selftest_gone",
        reason="Self-test entry naming a module that does not exist, to prove staleness is caught.",
    )
    if not any("_invariant_selftest_gone" in p for p in check()):
        failures.append("a stale registration was NOT caught")

    if failures:
        for f in failures:
            print(f"self-test FAILED: {f}", file=sys.stderr)  # noqa: T201
        return 1
    print(  # noqa: T201
        f"self-test OK: an undeclared package is caught in each of {', '.join(SOURCE_ROOTS)}, "
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
    n = len(discover(_REPO, SOURCE_ROOTS)[0])
    print(  # noqa: T201
        f"OK: all {n} packages under {', '.join(SOURCE_ROOTS)} declare an invariant or say why not."
    )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("FACTORY_INVARIANTS", "0")
    sys.exit(main())
