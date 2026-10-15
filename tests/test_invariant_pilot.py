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

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "apps" / "backend"
WEB_SERVER = REPO / "apps" / "web-server"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(WEB_SERVER))

import factory_invariants as fi  # noqa: E402
import framework_registry._invariants  # noqa: E402  -- registers
import framework_registry.loader  # noqa: E402
import merge._invariants  # noqa: E402  -- registers
import merge.semantic_analyzer  # noqa: E402
import pfactory_secrets._invariants  # noqa: E402,F401  -- registers
import plan._invariants  # noqa: E402  -- registers
import plan.enrich.base  # noqa: E402
import project  # noqa: E402
import providers._invariants  # noqa: E402  -- registers
import providers.factory  # noqa: E402
import security._invariants  # noqa: E402  -- registers
import security.hooks  # noqa: E402
import server._invariants  # noqa: E402  -- registers
import server.routes.mcp_rpc  # noqa: E402
import tools._invariants  # noqa: E402  -- registers
import tools.definitions  # noqa: E402
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
    assert "an undeclared package is caught" in r.stdout


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_invariant_ownership", REPO / "scripts" / "check_invariant_ownership.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_package_on_disk_is_discovered_and_declared() -> None:
    """Discovery must match the filesystem, so a new package cannot be skipped."""
    checker = _load_checker()
    found = set(checker.discover(REPO, checker.SOURCE_ROOTS)[0])
    on_disk = {
        p.parent.name for root in checker.SOURCE_ROOTS for p in (REPO / root).glob("*/__init__.py")
    }
    assert found == on_disk
    assert len(found) >= 32
    assert checker.check() == []


# ------------------------------------- the step-2 package checks actually FIRE


def _fires(live: fi.InvariantRegistry, module: str, needle: str) -> None:
    hits = [v for v in live.check_all() if v.module == module]
    assert any(needle in v.detail for v in hits), hits


def test_a_dangling_provider_alias_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(providers.factory._PROVIDER_ALIASES, "ghost", "no_such_provider")
    _fires(live, "providers.factory", "no_such_provider")


def test_a_command_without_a_validator_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    cmd = sorted(project.VALIDATED_COMMANDS)[0]
    monkeypatch.delitem(security.hooks.VALIDATORS, cmd)
    _fires(live, "security.hooks", repr(cmd))


def test_an_empty_frameworks_dir_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(framework_registry.loader, "_DEFAULT_FRAMEWORKS_DIR", tmp_path)
    _fires(live, "framework_registry.loader", "no framework descriptors")


def test_a_vanished_adapter_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = plan.enrich.base.available_adapters
    monkeypatch.setattr(
        plan.enrich.base, "available_adapters", lambda: [n for n in real() if n != "aws"]
    )
    _fires(live, "plan.registry", "infra:aws")


def test_an_advertised_tool_without_a_handler_fires(
    live: fi.InvariantRegistry, monkeypatch
) -> None:
    monkeypatch.setitem(tools.definitions._ALL_TOOLS, "Ghost", {})
    _fires(live, "tools.executor", "Ghost")


def test_an_mcp_handler_that_is_never_listed_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(server.routes.mcp_rpc._TOOL_HANDLERS, "ghost", lambda: None)
    _fires(live, "server.routes.mcp_rpc", "ghost")


def test_a_missing_tree_sitter_grammar_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(merge.semantic_analyzer.LANGUAGES_AVAILABLE, ".ts")
    _fires(live, "merge.semantic_analyzer", ".ts")


def test_tree_sitter_failing_to_import_fires(
    live: fi.InvariantRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(merge.semantic_analyzer, "TREE_SITTER_AVAILABLE", False)
    _fires(live, "merge.semantic_analyzer", "regex fallback")
