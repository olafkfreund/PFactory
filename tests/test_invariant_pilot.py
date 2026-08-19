"""Tests for the module-owned runtime invariant pilot (Factory#818).

Three things are under test, and the third is the one that matters:

1. the registry's own contract (uniqueness, the two declaration forms, config)
2. every real invariant FIRES when its relation is broken
3. the ownership checker CATCHES a module that declared nothing

(3) is what makes the pattern a system rather than four checks. Without it a new
module can be added and stay invisible to diagnostics, which is the failure the
whole pattern exists to prevent.
"""

# S603: the only subprocess calls run this repo's own checker via sys.executable,
# with no external input. PLC0415 is not triggered here; the path insert above
# is what makes the package importable.
# ruff: noqa: S603

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "apps" / "backend"
sys.path.insert(0, str(BACKEND))

import factory_invariants as fi  # noqa: E402
import pfactory_secrets._invariants  # noqa: E402,F401  -- registers
from pfactory_secrets import factory, refs  # noqa: E402


@pytest.fixture
def live() -> fi.InvariantRegistry:
    """The process registry with checks enabled, restored afterwards."""
    was = fi.registry.enabled
    fi.registry.enabled = True
    yield fi.registry
    fi.registry.enabled = was


# --------------------------------------------------------------------- registry


def test_a_module_cannot_register_twice() -> None:
    """Two declarations for one module would leave one silently never running."""
    r = fi.InvariantRegistry(enabled=True)
    r.register("m", reason="owns no observable relation because it holds no state at all")
    with pytest.raises(ValueError, match="already registered"):
        r.register("m", reason="a second declaration for the same module name here")


def test_a_declaration_must_be_exactly_one_form() -> None:
    r = fi.InvariantRegistry()
    with pytest.raises(ValueError, match="exactly one"):
        r.register("m")
    with pytest.raises(ValueError, match="exactly one"):
        r.register("m", lambda: iter(()), reason="both forms supplied at once here")


@pytest.mark.parametrize("bad", ["TODO", "n/a", "grandfathered", "none", "no state"])
def test_a_placeholder_reason_is_refused(bad: str) -> None:
    """The empty form is an architectural conclusion, not a placeholder."""
    r = fi.InvariantRegistry()
    with pytest.raises(ValueError, match="declared-empty reason"):
        r.register("m", reason=bad)


def test_disabled_registry_runs_nothing() -> None:
    """Default-off matters: an assertion that throws in a pod is a new failure mode."""
    r = fi.InvariantRegistry(enabled=False)
    r.register("m", lambda: iter(["always a violation"]))
    assert r.check_all() == []


def test_a_check_that_raises_is_a_violation_not_a_crash() -> None:
    """A broken check must not be indistinguishable from a clean run."""

    def boom():
        raise RuntimeError("kaboom")
        yield  # pragma: no cover

    r = fi.InvariantRegistry(enabled=True)
    r.register("m", boom)
    (v,) = r.check_all()
    assert "the check itself raised RuntimeError" in v.detail


# ------------------------------------------------------- the seam is clean today


def test_the_credential_seam_has_no_violations(live: fi.InvariantRegistry) -> None:
    assert live.check_all() == []


def test_every_module_declared_exactly_once(live: fi.InvariantRegistry) -> None:
    declared = live.registered()
    assert len(declared) == len({d.lower() for d in declared})
    assert len(live.executable()) >= 4, "the pilot's four real checks must be registered"


# --------------------------------------------- each real invariant actually FIRES


def test_a_dangling_alias_fires(live: fi.InvariantRegistry) -> None:
    factory._ALIASES["kms"] = "aws_kms"
    try:
        details = [v.detail for v in live.check_all()]
    finally:
        del factory._ALIASES["kms"]
    assert any("aws_kms" in d for d in details)


def test_a_dangling_ref_scheme_fires(live: fi.InvariantRegistry) -> None:
    """The cross-module relation: refs names a backend that factory owns."""
    refs._SCHEME_TO_BACKEND["oldvault"] = "vault_v1"
    try:
        details = [v.detail for v in live.check_all()]
    finally:
        del refs._SCHEME_TO_BACKEND["oldvault"]
    assert any("vault_v1" in d for d in details)


def test_removing_a_backend_fires_in_both_modules(live: fi.InvariantRegistry) -> None:
    """The case no single-file tool sees: a rename here, a dangling table there.

    refs.py never imports factory.py's table, so nothing else in the toolchain
    relates the two.
    """
    saved = factory._BACKEND_REGISTRY.pop("vault")
    try:
        modules = {v.module for v in live.check_all()}
    finally:
        factory._BACKEND_REGISTRY["vault"] = saved
    assert modules == {"pfactory_secrets.factory", "pfactory_secrets.refs"}


def test_a_backend_outside_the_package_fires(live: fi.InvariantRegistry) -> None:
    factory._BACKEND_REGISTRY["rogue"] = ("os.path", "Rogue")
    try:
        details = [v.detail for v in live.check_all()]
    finally:
        del factory._BACKEND_REGISTRY["rogue"]
    assert any("points outside" in d for d in details)


def test_verify_all_raises_attributed_to_the_owning_module(live: fi.InvariantRegistry) -> None:
    factory._ALIASES["kms"] = "aws_kms"
    try:
        with pytest.raises(fi.InvariantError) as exc:
            live.verify_all()
    finally:
        del factory._ALIASES["kms"]
    assert exc.value.module == "pfactory_secrets.factory"


# ------------------------------------------------------ the ownership check works


def test_ownership_checker_passes_on_the_real_tree() -> None:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_invariant_ownership.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_ownership_checker_can_fail() -> None:
    """Its --self-test writes an undeclared module and asserts it is caught.

    Without this, "every module declares" is a claim about 17 files that could
    silently become a claim about 17 of 18.
    """
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_invariant_ownership.py"), "--self-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "an undeclared module is caught" in r.stdout
