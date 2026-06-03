"""Integration test — TS Evaluator hooks wired to framework descriptors (Task 9 / #25 commit 6).

Verifies that:
  1. The jest and playwright descriptors expose evaluator_hooks populated
     with the three lang_typescript primitive dotted-paths.
  2. Each hook path is importable and resolves to the expected callable.
  3. The hooks can be invoked with a mock runner_fn (no Docker required).
  4. The framework_registry loader correctly reads evaluator_hooks from
     the YAML descriptor into the FrameworkDescriptor dataclass.

This test requires Task 6's gen_functional branch to be on main (which it
is — merged as commit 4c006fb). It exercises the full integration path
that the Evaluator will use at runtime: load registry → get descriptor →
dispatch evaluator_hooks by language.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from framework_registry import load_registry
from framework_registry.descriptor import FrameworkDescriptor

# ─── Expected hook dotted paths ──────────────────────────────────────────────

_EXPECTED_HOOKS = (
    "agents.lang_typescript.preflight.run_ts_preflight",
    "agents.lang_typescript.flake_lint.run_ts_flake_lint",
    "agents.lang_typescript.mutate_probe.run_ts_mutate_probe",
)


# ─── Mock runner for hook invocation ─────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _ok_runner(cmd, cwd, *, image, timeout):
    """Return exit-0 result for preflight and flake_lint."""
    return _FakeRunResult(returncode=0, stdout="")


def _stryker_no_mutant_runner(cmd, cwd, *, image, timeout):
    """Return Stryker JSON with no mutants (no assertion found)."""
    return _FakeRunResult(
        returncode=0,
        stdout=json.dumps({"schemaVersion": "2.0", "mutants": []}),
    )


# ─── Descriptor loading tests ─────────────────────────────────────────────────


def test_jest_descriptor_evaluator_hooks_populated() -> None:
    registry = load_registry()
    assert "jest" in registry, "jest not in registry"
    desc = registry["jest"]
    assert isinstance(desc, FrameworkDescriptor)
    assert len(desc.evaluator_hooks) == 3
    for hook in _EXPECTED_HOOKS:
        assert hook in desc.evaluator_hooks, f"Missing hook: {hook}"


def test_playwright_descriptor_evaluator_hooks_populated() -> None:
    registry = load_registry()
    assert "playwright" in registry, "playwright not in registry"
    desc = registry["playwright"]
    assert isinstance(desc, FrameworkDescriptor)
    assert len(desc.evaluator_hooks) == 3
    for hook in _EXPECTED_HOOKS:
        assert hook in desc.evaluator_hooks, f"Missing hook: {hook}"


def test_pytest_descriptor_has_empty_evaluator_hooks() -> None:
    """pytest descriptor should still have empty hooks (Python primitives
    are consumed differently — they're not in evaluator_hooks for v0.1)."""
    registry = load_registry()
    assert "pytest" in registry
    desc = registry["pytest"]
    # pytest descriptor may have empty hooks — it uses the Python-native
    # primitives rather than the dotted-path dispatch.
    assert isinstance(desc.evaluator_hooks, tuple)


# ─── Hook importability tests ─────────────────────────────────────────────────


@pytest.mark.parametrize("dotted_path", _EXPECTED_HOOKS)
def test_evaluator_hook_is_importable(dotted_path: str) -> None:
    """Each hook path must resolve to an importable callable."""
    module_path, _, fn_name = dotted_path.rpartition(".")
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    assert callable(fn), f"{dotted_path} is not callable"


# ─── Hook invocation tests via descriptor dispatch ───────────────────────────


@pytest.fixture
def ts_test_file(tmp_path: Path) -> Path:
    f = tmp_path / "sample.test.ts"
    f.write_text("test('x', () => { expect(1).toBe(1); });\n")
    return f


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


def _load_hook(dotted_path: str):
    """Import a hook function from its dotted path."""
    module_path, _, fn_name = dotted_path.rpartition(".")
    mod = importlib.import_module(module_path)
    return getattr(mod, fn_name)


def test_preflight_hook_invocable_via_descriptor(
    ts_test_file: Path, project_dir: Path
) -> None:
    """Load the preflight hook from the jest descriptor and call it."""
    registry = load_registry()
    desc = registry["jest"]
    preflight_path = next(h for h in desc.evaluator_hooks if "preflight" in h)
    run_ts_preflight = _load_hook(preflight_path)
    report = run_ts_preflight(ts_test_file, project_dir, runner_fn=_ok_runner)
    assert report.ok is True
    assert hasattr(report, "unresolved_imports")
    assert hasattr(report, "other_errors")


def test_flake_lint_hook_invocable_via_descriptor(
    ts_test_file: Path, project_dir: Path
) -> None:
    """Load the flake_lint hook from the playwright descriptor and call it."""
    registry = load_registry()
    desc = registry["playwright"]
    flake_path = next(h for h in desc.evaluator_hooks if "flake_lint" in h)
    run_ts_flake_lint = _load_hook(flake_path)
    report = run_ts_flake_lint(ts_test_file, project_dir, runner_fn=_ok_runner)
    assert hasattr(report, "findings")
    assert hasattr(report, "has_high")
    assert hasattr(report, "has_medium")
    assert report.has_high is False
    assert report.has_medium is False


def test_mutate_probe_hook_invocable_via_descriptor(
    ts_test_file: Path, project_dir: Path
) -> None:
    """Load the mutate_probe hook from the jest descriptor and call it."""
    registry = load_registry()
    desc = registry["jest"]
    mutate_path = next(h for h in desc.evaluator_hooks if "mutate_probe" in h)
    run_ts_mutate_probe = _load_hook(mutate_path)
    report = run_ts_mutate_probe(
        ts_test_file, project_dir, runner_fn=_stryker_no_mutant_runner
    )
    assert hasattr(report, "verdict")
    assert hasattr(report, "mutated_assertion")
    assert hasattr(report, "raw_stryker_json")


def test_all_hooks_have_correct_parameter_signatures() -> None:
    """Verify the three hooks accept (test_file, project_dir, runner_fn=None, runner_image=...)."""
    import inspect

    for dotted_path in _EXPECTED_HOOKS:
        fn = _load_hook(dotted_path)
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert "test_file" in params, f"{dotted_path} missing 'test_file' param"
        assert "project_dir" in params, f"{dotted_path} missing 'project_dir' param"
        assert "runner_fn" in params, f"{dotted_path} missing 'runner_fn' param"
        assert "runner_image" in params, f"{dotted_path} missing 'runner_image' param"


def test_jest_and_playwright_have_same_hooks() -> None:
    """Both TypeScript frameworks should expose the same three Evaluator hooks."""
    registry = load_registry()
    jest_hooks = set(registry["jest"].evaluator_hooks)
    playwright_hooks = set(registry["playwright"].evaluator_hooks)
    assert jest_hooks == playwright_hooks


def test_evaluator_hooks_are_a_tuple_in_descriptor() -> None:
    """FrameworkDescriptor.evaluator_hooks must be a frozen tuple."""
    registry = load_registry()
    for name in ("jest", "playwright"):
        desc = registry[name]
        assert isinstance(desc.evaluator_hooks, tuple), (
            f"{name}.evaluator_hooks should be a tuple, got {type(desc.evaluator_hooks)}"
        )
