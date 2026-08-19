#!/usr/bin/env python3
"""Whole-package ``mypy --strict`` gate for packages already at zero (PFactory#468).

WHY THIS IS NOT THE RATCHET, AND MUST NOT BECOME IT
---------------------------------------------------
``scripts/ratchet_lint.py`` gates the files a PR CHANGES: a touched file may not
gain ``mypy --strict`` errors. That is the right model for a 2,700-error backlog,
and it is untouched by this gate.

It is not enough for a package that has been driven to ZERO. The ratchet cannot
tell a clean package from a filthy one — both are merely "no worse than base" —
so nothing keeps finished work finished. A file that never appears in a diff is
never re-measured, and a package can regrow through a config change, a new
transitive import, or a stub bump without any PR touching one of its files.

So the packages listed in ``mypy-strict-packages.txt`` are checked WHOLE, on
every PR, and must report zero. The list can only grow; the count for anything
on it can only be zero. That is the one-way ratchet #468 asked for.

Measurement goes through ``ratchet_lint.run_mypy`` — the same cwd, MYPYPATH,
config and flags the per-file ratchet uses. A second copy of that argv would
drift, and then a file could be clean under one gate and dirty under the other.

Usage:
    python scripts/mypy_strict_packages.py [--list <file>] [--mypy-config <ini>]
    python scripts/mypy_strict_packages.py --self-test

Exit code 0 if every listed package reports zero; 1 otherwise.
"""

# A CLI gate: `print` IS its reporting surface, same as ratchet_lint.py.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ratchet_lint import MYPY_CONFIG_DEFAULT, run_mypy

LIST_DEFAULT = "mypy-strict-packages.txt"

# The import roots the ratchet uses. A listed directory is checked from inside
# the LONGEST of these that contains it, mirroring ratchet_lint.owning_package:
# a package type-checked against the wrong root resolves its first-party imports
# against the wrong tree (Factory#384).
ROOTS = ("apps/backend", "apps/web-server", "scripts")

_ERROR_RE = re.compile(r"^(?P<path>.+?):\d+: error:")

# Planted by --self-test. A module name no real package would use, so a stray
# copy left behind by a killed run is obvious rather than mysterious.
_SELF_TEST_MODULE = "_mypy_strict_gate_selftest.py"
_SELF_TEST_SOURCE = 'def planted() -> int:\n    return "not an int"\n'


def read_list(path: Path) -> list[str]:
    """Directories named in *path*, comments and blank lines stripped."""
    out: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def root_for(entry: str) -> str | None:
    """The import root *entry* lives under, or None if it is outside all of them."""
    target = Path(entry)
    matches = [r for r in ROOTS if Path(r) in target.parents]
    return max(matches, key=len) if matches else None


def package_errors(entry: str, mypy_config: str) -> tuple[int, str]:
    """(error count, mypy output) for the whole directory *entry*.

    Only errors located INSIDE *entry* are counted. mypy follows imports, and an
    error in a module this package merely imports belongs to that module's own
    package — attributing it here would make this gate fail for work nobody in
    this package can do, which is how a gate gets disabled.
    """
    root = root_for(entry)
    if root is None:
        raise ValueError(f"{entry}: not under any of {ROOTS}")
    rel = str(Path(entry).relative_to(root))
    res = run_mypy(root, rel, mypy_config)
    prefix = rel.rstrip("/") + "/"
    count = 0
    for line in res.stdout.splitlines():
        m = _ERROR_RE.match(line)
        if m is not None and m.group("path").startswith(prefix):
            count += 1
    # An exit status with no located error line means mypy did not run (a bad
    # config, a missing plugin, an unresolvable target). Zero errors and "never
    # measured anything" look identical in the count, so separate them here
    # rather than reporting a green gate that examined nothing.
    if res.returncode != 0 and count == 0:
        raise RuntimeError(
            f"{entry}: mypy exited {res.returncode} without reporting a located "
            f"error, so it measured nothing:\n{res.stdout}\n{res.stderr}"
        )
    return count, res.stdout


def check(entries: list[str], mypy_config: str) -> list[str]:
    """Failure messages for *entries*; empty means every one is at zero."""
    failures: list[str] = []
    for entry in entries:
        if not Path(entry).is_dir():
            # An entry that matches nothing fails, so the list cannot rot into a
            # set of names that stopped meaning anything (Factory#788).
            failures.append(f"{entry}: listed as strict-clean but is not a directory")
            continue
        try:
            count, output = package_errors(entry, mypy_config)
        except (ValueError, RuntimeError) as exc:
            failures.append(str(exc))
            continue
        if count:
            failures.append(f"{entry}: {count} mypy --strict errors (must be 0)\n{output}")
        else:
            print(f"  ok  {entry}")
    return failures


def self_test(entries: list[str], mypy_config: str) -> int:
    """Prove the gate can go RED, then that it goes green again.

    A gate that has only ever been observed passing is not evidence of anything
    (Factory#694/#697, and the same reason security_lint.py carries a --self-test).
    This plants a genuinely ill-typed module inside the first listed package,
    requires a failure, removes it, and requires a pass.
    """
    if not entries:
        print("self-test: the strict-package list is empty, so there is nothing to mutate")
        return 1
    victim = Path(entries[0]) / _SELF_TEST_MODULE
    try:
        victim.write_text(_SELF_TEST_SOURCE)
        if not check([entries[0]], mypy_config):
            print(f"self-test FAILED: {entries[0]} still reports zero with {victim} planted.")
            print("The gate cannot see a type error in a package it claims to enforce.")
            return 1
        print(f"self-test: {entries[0]} went RED with a planted type error, as required")
    finally:
        victim.unlink(missing_ok=True)
    if check([entries[0]], mypy_config):
        print(f"self-test FAILED: {entries[0]} is still red after removing {victim}.")
        return 1
    print(f"self-test: {entries[0]} is green again with it removed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", default=LIST_DEFAULT, help="file naming the strict-clean packages")
    ap.add_argument("--mypy-config", default=MYPY_CONFIG_DEFAULT, help="mypy config file")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="prove the gate goes red on a planted type error, then green without it",
    )
    args = ap.parse_args(argv)

    list_path = Path(args.list)
    if not list_path.is_file():
        print(f"{args.list}: no such file — the strict-package gate has nothing to enforce.")
        return 1
    entries = read_list(list_path)
    if args.self_test:
        return self_test(entries, args.mypy_config)

    if not entries:
        # Zero items is a zero-item result, not a pass.
        print(f"{args.list} names no packages, so this gate measured NOTHING.")
        return 1

    print(f"mypy --strict, whole package, must be 0 — {len(entries)} package(s):")
    failures = check(entries, args.mypy_config)
    if failures:
        print("\nstrict-package gate FAILED:")
        for f in failures:
            print(f"  {f}")
        print(
            "\nThese packages are on the one-way ratchet in "
            f"{args.list}: they reached zero and may not regress. Fix the types "
            "(do not add a bare `type: ignore`), or say in the PR body why the "
            "package is being taken off the list."
        )
        return 1
    print(f"\nstrict-package gate OK: {len(entries)} package(s) at zero.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
