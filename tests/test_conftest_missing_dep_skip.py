"""The split-venv missing-dep skip must cover every dep AND every test tree.

A test module importing a dep this venv lacks must be *ignored at collection*.
If it is not, the import raises during collection — and a collection error is
not a skip: pytest aborts the whole invocation, so `pytest tests/
apps/web-server/tests/` (what ci.yml runs) reports nothing at all rather than
the 4500+ tests that would have run. One uninstalled optional dependency in one
module silences the entire suite (#453).

CI installs both requirement files, so CI never sees it. That is exactly why
the mechanism has to be checked here rather than trusted.

WHY THE MAIN TEST SHELLS OUT. The tempting test — "every distribution in
requirements.txt maps to a name the skip list knows" — cannot be written
honestly. A distribution's import name is not derivable from its package name
(`python-jose` -> `jose`), so the mechanism carries TWO guesses per dist and
the test would have to make the same two guesses. It then passes whenever the
mechanism's guesses match the test's, which is always, including when both are
wrong: mutating the derivation left that test green while the real suite went
back to aborting. Collecting for real is the only formulation that fails when
the mechanism fails.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "missing_deps_under_test", _ROOT / "tests" / "missing_deps.py"
)
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)

# The two test trees the CI command names.
_TEST_TREES = (_ROOT / "tests", _ROOT / "apps" / "web-server" / "tests")


def test_web_server_tree_collects_without_error():
    """`apps/web-server/tests/` must collect cleanly in THIS venv, whatever it has.

    The tree that broke: it is a SIBLING of `tests/`, so `tests/conftest.py`'s
    hooks never applied to it, and `test_tracing.py`'s `from
    opentelemetry.sdk.trace.export import ...` aborted the whole run.

    A collection error here is a suite-wide outage, so it is checked for real
    rather than predicted.
    """
    proc = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(_TEST_TREES[1])],
        cwd=_ROOT,
        capture_output=True,
        text=True,
    )
    # Match pytest's own collection-error wording, not any line containing
    # "error" — deprecation warnings cite https://errors.pydantic.dev/ and would
    # make this assert fire on a perfectly clean collection.
    assert "during collection" not in proc.stdout, (
        f"apps/web-server/tests/ did not collect cleanly — in a real run this "
        f"aborts every other test too:\n{proc.stdout[-3000:]}"
    )
    assert proc.returncode == 0, proc.stdout[-3000:]


def test_both_test_trees_register_the_hook():
    """A conftest's hooks stop at its own directory — so each tree needs one.

    Structural companion to the collection test above: it names the cause where
    that one names the symptom.
    """
    for tree in _TEST_TREES:
        conftest = tree / "conftest.py"
        assert conftest.is_file(), f"{tree} has no conftest.py"
        assert "pytest_ignore_collect" in conftest.read_text(encoding="utf-8"), (
            f"{conftest} does not register pytest_ignore_collect, so a module "
            f"in {tree} importing an absent dep will abort collection"
        )


def test_a_module_importing_an_absent_dep_is_ignored(tmp_path):
    """End to end on the mechanism, in whichever direction this venv allows."""
    if md.MISSING:
        module = tmp_path / "test_probe.py"
        module.write_text(f"from {md.MISSING[0]}.sub.deep import Thing\n")
        assert md.should_ignore(module) is True
    # A module importing nothing absent is never ignored, in any venv.
    plain = tmp_path / "test_plain.py"
    plain.write_text("import json\n")
    assert md.should_ignore(plain) is not True


def test_import_regex_matches_a_submodule_import():
    """`from opentelemetry.sdk.trace.export import X` must match.

    The real offending line is a deep submodule import; a pattern anchored on
    the full module path would miss it. And a name that merely starts with the
    same letters must NOT match, or the skip over-reaches.
    """
    if md.IMPORT_RE is None:
        return  # every web-server dep is installed here; nothing to match
    absent = md.MISSING[0]
    assert md.IMPORT_RE.match(f"from {absent}.sub.deep import Thing")
    assert md.IMPORT_RE.match(f"import {absent}")
    assert not md.IMPORT_RE.match(f"import {absent}xyz")
