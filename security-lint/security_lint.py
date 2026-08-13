#!/usr/bin/env python3
"""Whole-repo, BLOCKING security-sink lint gate with a per-finding allowlist.

CANONICAL. This file lives in the Factory hub at ``shared/factory-seclint/`` and
is vendored byte-exact by every service into ``security-lint/``. One engine, not
five copies: fix it here and re-vendor, never patch a service's copy.

WHY THIS EXISTS
---------------
The fleet's coding standard bans pickle/eval/shell-on-untrusted-data (rule 1.4)
and the ``S`` ruleset is selected in ``standards/ruff.toml``. Both true, and a
``pickle.load`` on a cache file still lived in a service for months. The reason
is that enforcement is DIFF-SCOPED — a strict whole-repo ``ruff check`` has
~6,500 legacy violations and would be permanently red — so the model is "legacy
is fixed on touch". That is right for style debt and wrong for security sinks,
because *fixed on touch* means *never* for code nobody touches, and untouched
code is where old vulnerabilities live.

So this gate is the narrow complement: a small, curated set of sinks
(``ruff-security.toml`` beside this file), enforced across the WHOLE repo,
blocking, with every pre-existing finding written down individually rather than
waved through by a category ignore.

HOW IT CANNOT BECOME A RUBBER STAMP
-----------------------------------
Four properties, each of which is a way this gate fails rather than drifts:

1. **An allowlist entry needs an issue reference.** No issue, gate red. A
   grandfathered finding with nobody's name on it is a permanent exception
   wearing a temporary hat.
2. **An entry that matches nothing is an error.** When a finding is fixed, its
   entry must go. The list can therefore only ratchet DOWN; it cannot quietly
   retain cover for code that no longer exists.
3. **An entry covers a COUNT, not a file.** ``count = 1`` allows exactly one
   occurrence of that rule in that file. Adding a second ``pickle.load`` to an
   already-allowlisted file turns the gate red.
4. **Adding an entry is a diff.** In the repo's own
   ``security-lint-allowlist.toml``, reviewed like code.

On expiry: an optional ``expires`` date is supported and enforced (see
``_check_expiry``). It is deliberately OPTIONAL rather than mandatory. A
mandatory hard expiry reddens ``main`` at midnight on a date, with no code
change, blocking every unrelated PR until someone edits a file under pressure —
which is how expiries get bumped by a year instead of honoured. Properties 1-3
above are the load-bearing anti-permanence mechanism; ``expires`` is for the
cases where a real date is known (a dependency bump, a scheduled migration).

HONEST OUTPUT (standard rule 4.10)
----------------------------------
"If this had done nothing at all, would the output look different?" A gate that
prints "security lint passed" after ruff failed to start has reported on the
process, not the artefact. So: the ruff invocation's exit code is checked (ruff
exits 0 for clean, 1 for findings, 2 for a bad invocation), the *number of files
scanned* is derived and printed, and a scan that reached zero files is a failure
regardless of how few findings it produced.

Stdlib only — ``tomllib`` for the allowlist, no PyYAML — so the CI job needs
nothing installed but ruff itself.

Usage:
    python security-lint/security_lint.py [--allowlist PATH] [--config PATH]
                                          [--ruff RUFF] [TARGET ...]

Exit 0 when every finding is accounted for; 1 otherwise; 2 on bad invocation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _HERE / "ruff-security.toml"

# `Repo#123`. Deliberately strict: "see the security channel" is not a reference
# anyone can reopen in two years.
_ISSUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*#\d+$")

_EXIT_OK = 0
_EXIT_FAIL = 1
_EXIT_BAD_INVOCATION = 2


def _emit(message: str = "") -> None:
    # This is a CLI gate; its stdout report IS the deliverable.
    print(message)  # noqa: T201


@dataclass(frozen=True)
class Entry:
    """One grandfathered finding."""

    path: str
    rule: str
    count: int
    reason: str
    issue: str
    expires: str | None


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    line: int
    message: str


def load_allowlist(path: Path) -> tuple[list[Entry], list[str]]:
    """Parse and VALIDATE the allowlist. Returns (entries, problems).

    A malformed allowlist is a gate failure, not a warning. The failure mode
    being defended against is a typo that makes an entry match nothing and
    therefore silently widen nothing — which would be fine — sitting next to a
    typo that makes a REAL finding look allowlisted.
    """
    problems: list[str] = []
    if not path.exists():
        # A missing allowlist is legitimate: it means nothing is grandfathered.
        return [], problems

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [], [f"{path}: cannot be parsed: {exc}"]

    entries: list[Entry] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw.get("allow", [])):
        where = f"{path}: [[allow]] #{index + 1}"
        if not isinstance(item, dict):
            problems.append(f"{where}: not a table")
            continue

        rule_path = item.get("path")
        rule = item.get("rule")
        reason = item.get("reason")
        issue = item.get("issue")

        for field, value in (("path", rule_path), ("rule", rule), ("reason", reason)):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}: `{field}` is required and must be a non-empty string")

        # The no-orphan check. Called out separately because it is the one that
        # keeps this file from becoming a permanent ignore list.
        if not isinstance(issue, str) or not _ISSUE_RE.match(issue):
            problems.append(
                f"{where}: `issue` is required and must look like `Repo#123` "
                f"(got {issue!r}). An allowlisted security finding with no issue "
                "to track it is a permanent exception, which is the thing this "
                "gate exists to prevent."
            )

        count = item.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            problems.append(f"{where}: `count` must be a positive integer (got {count!r})")
            count = 1

        expires = item.get("expires")
        if expires is not None and not isinstance(expires, str):
            problems.append(f"{where}: `expires` must be a YYYY-MM-DD string")
            expires = None

        if not (isinstance(rule_path, str) and isinstance(rule, str)):
            continue

        key = (rule_path, rule)
        if key in seen:
            problems.append(f"{where}: duplicate entry for {rule_path} / {rule}")
        seen.add(key)

        entries.append(
            Entry(
                path=rule_path,
                rule=rule,
                count=count,
                reason=str(reason),
                issue=str(issue),
                expires=expires,
            )
        )

    return entries, problems


def _check_expiry(entries: list[Entry], today: dt.date) -> list[str]:
    problems: list[str] = []
    for entry in entries:
        if entry.expires is None:
            continue
        try:
            when = dt.date.fromisoformat(entry.expires)
        except ValueError:
            problems.append(f"{entry.path} / {entry.rule}: `expires` is not a YYYY-MM-DD date")
            continue
        if when < today:
            problems.append(
                f"{entry.path} / {entry.rule}: allowlist entry expired on {entry.expires} "
                f"({entry.issue}). Fix the finding or renew the entry deliberately."
            )
    return problems


def run_ruff(ruff: str, config: Path, targets: list[str]) -> tuple[list[Finding], int]:
    """Run ruff and return (findings, files_scanned).

    Raises SystemExit(2) if ruff could not run — a gate that cannot read its
    artefact must fail, not pass (rule 4.7).
    """
    argv = [
        ruff,
        "check",
        "--no-cache",
        "--config",
        str(config),
        "--output-format",
        "json",
        *targets,
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    if proc.returncode not in (0, 1):
        _emit("SECURITY LINT: ruff could not run — the gate FAILS rather than passing.")
        _emit(f"  argv: {' '.join(argv)}")
        _emit(proc.stderr.strip())
        raise SystemExit(_EXIT_BAD_INVOCATION)

    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        _emit(f"SECURITY LINT: ruff output was not JSON ({exc}); failing closed.")
        _emit(proc.stdout[:2000])
        raise SystemExit(_EXIT_BAD_INVOCATION) from exc

    findings = [
        Finding(
            path=_relative(item["filename"]),
            rule=str(item["code"]),
            line=int((item.get("location") or {}).get("row") or 0),
            message=str(item.get("message", "")),
        )
        for item in payload
    ]

    # Files scanned: derived independently of the findings, so "0 findings"
    # cannot be produced by "0 files". This is the rule-4.10 artefact.
    listed = subprocess.run(  # noqa: S603
        [ruff, "check", "--no-cache", "--config", str(config), "--show-files", *targets],
        capture_output=True,
        text=True,
        check=False,
    )
    files_scanned = len([ln for ln in listed.stdout.splitlines() if ln.strip()])
    return findings, files_scanned


def _relative(filename: str) -> str:
    try:
        return Path(filename).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return filename


def reconcile(
    findings: list[Finding], entries: list[Entry]
) -> tuple[list[Finding], list[Entry], list[str]]:
    """Split findings into (unallowlisted, stale entries, over-count problems)."""
    actual: Counter[tuple[str, str]] = Counter((f.path, f.rule) for f in findings)
    allowed = {(e.path, e.rule): e for e in entries}

    unallowlisted = [f for f in findings if (f.path, f.rule) not in allowed]

    stale = [e for e in entries if actual[(e.path, e.rule)] == 0]

    over: list[str] = []
    for key, entry in allowed.items():
        seen = actual[key]
        if seen > entry.count:
            over.append(
                f"{entry.path}: {seen} x {entry.rule} but the allowlist covers "
                f"{entry.count} ({entry.issue}). A NEW occurrence of an already-"
                "grandfathered sink is still new."
            )
    return unallowlisted, stale, over


_SELF_TEST_SINK = "import pickle\n\n\ndef load(f):\n    return pickle.load(f)\n"


def self_test(ruff: str, config: Path) -> int:
    """Prove this gate can go RED, in the same job that reports it green.

    Rule 4.10 again, applied to the gate itself rather than to the code it
    reads. A green security-lint run is worth nothing if the tool would have
    been green against a planted `pickle.load` too — which is exactly what a
    mis-typed rule code, a config that failed to load, or a target path that
    matches no files would produce. So: plant the primitive in a scratch
    directory, require red; remove it, require green. Both directions, because
    a gate that is always red is as useless as one that is always green.

    This travels with the engine deliberately. The hub has a full pytest suite
    (tests/test_security_lint.py); the services vendor only this file, and the
    mutation proof is the half they must not be missing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        planted = scratch / "seclint_self_test.py"
        planted.write_text(_SELF_TEST_SINK, encoding="utf-8")

        findings, scanned = _run_in(ruff, config, scratch)
        if scanned == 0:
            _emit("SELF-TEST FAILED: the planted file was not scanned at all.")
            return _EXIT_FAIL
        if not any(f.rule == "S301" for f in findings):
            _emit(
                "SELF-TEST FAILED: a planted `pickle.load` did NOT produce S301. "
                "This gate cannot fail, so its green result means nothing "
                "(standard rule 4.10)."
            )
            return _EXIT_FAIL

        planted.unlink()
        (scratch / "seclint_self_test.py").write_text("x = 1\n", encoding="utf-8")
        findings, _ = _run_in(ruff, config, scratch)
        if findings:
            _emit(f"SELF-TEST FAILED: clean scratch file still reported {findings}.")
            return _EXIT_FAIL

    _emit("SELF-TEST PASSED: a planted `pickle.load` turns this gate red; removing it turns")
    _emit("it green. The verdict below is therefore a verdict and not a formality.")
    _emit()
    return _EXIT_OK


def _run_in(ruff: str, config: Path, cwd: Path) -> tuple[list[Finding], int]:
    """run_ruff, but rooted at *cwd* — the self-test must not scan the repo."""
    proc = subprocess.run(  # noqa: S603
        [
            ruff,
            "check",
            "--no-cache",
            "--config",
            str(config),
            "--output-format",
            "json",
            ".",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        _emit(f"SELF-TEST: ruff could not run: {proc.stderr.strip()}")
        raise SystemExit(_EXIT_BAD_INVOCATION)
    payload = json.loads(proc.stdout or "[]")
    findings = [
        Finding(
            path=str(item["filename"]),
            rule=str(item["code"]),
            line=int((item.get("location") or {}).get("row") or 0),
            message=str(item.get("message", "")),
        )
        for item in payload
    ]
    listed = subprocess.run(  # noqa: S603
        [ruff, "check", "--no-cache", "--config", str(config), "--show-files", "."],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return findings, len([ln for ln in listed.stdout.splitlines() if ln.strip()])


def _report(
    allowlist: str,
    problems: list[str],
    unallowlisted: list[Finding],
    stale: list[Entry],
    over: list[str],
) -> bool:
    """Print every failure category. Returns True when there is nothing to say."""
    if problems:
        _emit("ALLOWLIST IS INVALID:")
        for problem in problems:
            _emit(f"  - {problem}")
        _emit()

    if unallowlisted:
        _emit(f"NEW SECURITY-SINK FINDINGS ({len(unallowlisted)}):")
        for finding in sorted(unallowlisted, key=lambda f: (f.path, f.line)):
            _emit(f"  {finding.path}:{finding.line}: {finding.rule} {finding.message}")
        _emit()
        _emit("  Fix it. If it is genuinely not exploitable, add an entry to")
        _emit(f"  {allowlist} with a one-line reason AND an issue reference:")
        example = unallowlisted[0]
        _emit("")
        _emit("      [[allow]]")
        _emit(f'      path = "{example.path}"')
        _emit(f'      rule = "{example.rule}"')
        _emit('      reason = "why this specific call cannot be reached by untrusted data"')
        _emit('      issue = "Repo#123"')
        _emit()

    if over:
        _emit("ALLOWLIST OVERRUN:")
        for problem in over:
            _emit(f"  - {problem}")
        _emit()

    if stale:
        _emit("STALE ALLOWLIST ENTRIES (the finding is gone — delete the entry):")
        for entry in stale:
            _emit(f"  - {entry.path} / {entry.rule} ({entry.issue})")
        _emit()
        _emit("  This gate ratchets DOWN only. An entry that matches nothing is")
        _emit("  standing cover for a sink that no longer exists.")
        _emit()

    return not (problems or unallowlisted or over or stale)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="*", default=["."])
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--allowlist", type=Path, default=Path("security-lint-allowlist.toml"))
    parser.add_argument("--ruff", default="ruff")
    parser.add_argument(
        "--today", default=None, help="override today's date (YYYY-MM-DD), for tests"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the gate can go red (plants a pickle.load in a tempdir), then exit",
    )
    args = parser.parse_args(argv)
    targets = args.targets or ["."]

    if not args.config.exists():
        _emit(f"SECURITY LINT: config not found at {args.config}; failing closed.")
        return _EXIT_BAD_INVOCATION

    if args.self_test:
        return self_test(args.ruff, args.config)

    entries, problems = load_allowlist(args.allowlist)
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.UTC).date()
    problems += _check_expiry(entries, today)

    findings, files_scanned = run_ruff(args.ruff, args.config, targets)
    unallowlisted, stale, over = reconcile(findings, entries)

    rules = sorted({f.rule for f in findings})
    _emit("=== Factory security-sink lint (whole-repo, blocking) ===")
    _emit(f"  config      : {args.config}")
    _emit(f"  allowlist   : {args.allowlist} ({len(entries)} grandfathered)")
    _emit(f"  files scanned: {files_scanned}")
    _emit(f"  findings     : {len(findings)}" + (f" across {rules}" if rules else ""))
    _emit()

    if files_scanned == 0:
        _emit(
            "SECURITY LINT FAILED: ruff scanned ZERO files. A gate with no input "
            "is not a clean repo (rule 4.10) — check the target paths."
        )
        return _EXIT_FAIL

    ok = _report(str(args.allowlist), problems, unallowlisted, stale, over)

    if ok:
        _emit(
            f"PASS: {len(findings)} finding(s), all {len(entries)} accounted for by "
            "an allowlist entry with an issue reference."
        )
        return _EXIT_OK
    return _EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
