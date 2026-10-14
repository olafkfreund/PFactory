"""Test suite for the framework registry — Task 1 (#17).

Covers:
  - happy validate_descriptor for each of the 3 descriptors
  - missing-field rejection (parametrized over every required field)
  - version_range parsing
  - unknown-lane rejection
  - bad coverage_strategy rejection
  - load_registry happy path returns 3 keys
  - duplicate-name detection
  - missing frameworks_dir error
  - get_descriptor lookup + KeyError
  - frozen-dataclass immutability
  - end-to-end load of the real frameworks/ dir

All synthetic tests use tmp_path + monkeypatch to avoid touching the real
frameworks/ directory.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from framework_registry import (
    FrameworkDescriptor,
    FrameworkDescriptorError,
    FrameworkRegistryError,
    RuntimeSpec,
    get_descriptor,
    load_registry,
    validate_descriptor,
)
from test_plan.enums import Lane

# ---------------------------------------------------------------------------
# Shared minimal valid descriptor data
# ---------------------------------------------------------------------------

_PYTEST_DICT: dict[str, Any] = {
    "name": "pytest",
    "language": "python",
    "lanes": ["unit"],
    "version_range": ">=7.0,<10.0",
    "runtime": {
        "image": "tfactory-runner-pytest:latest",
        "entrypoint": ["python", "-m", "pytest"],
    },
    "manifest_signals": ["requirements.txt:pytest"],
    "test_path_conventions": ["tests/**/test_*.py"],
    "coverage_strategy": "cobertura",
    "context_block": "Use pytest fixtures for setup.",
}

_JEST_DICT: dict[str, Any] = {
    "name": "jest",
    "language": "typescript",
    "lanes": ["unit"],
    "version_range": ">=27.0,<31.0",
    "runtime": {
        "image": "tfactory-runner-jest:latest",
        "entrypoint": ["npx", "jest", "--ci"],
    },
    "manifest_signals": ["package.json:devDependencies.jest"],
    "test_path_conventions": ["**/*.test.ts"],
    "coverage_strategy": "lcov",
    "context_block": "Use Jest describe/it blocks.",
}

_PLAYWRIGHT_DICT: dict[str, Any] = {
    "name": "playwright",
    "language": "typescript",
    "lanes": ["browser"],
    "version_range": ">=1.40,<2.0",
    "runtime": {
        "image": "tfactory-runner-playwright:latest",
        "entrypoint": ["npx", "playwright", "test"],
    },
    "manifest_signals": ["package.json:devDependencies.@playwright/test"],
    "test_path_conventions": ["tests/e2e/**/*.spec.ts"],
    "coverage_strategy": "skip",
    "context_block": "Use page.getByRole() for selectors.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frameworks_dir(tmp_path: Path, descriptors: dict[str, dict]) -> Path:
    """Create a frameworks/ directory structure with the given descriptors.

    Args:
        tmp_path: Base directory from pytest.
        descriptors: Mapping from framework name to descriptor dict.

    Returns:
        Path to the created frameworks/ directory.
    """
    fw_dir = tmp_path / "frameworks"
    for name, data in descriptors.items():
        fw_sub = fw_dir / name
        fw_sub.mkdir(parents=True)
        (fw_sub / "descriptor.yaml").write_text(
            yaml.dump(data), encoding="utf-8"
        )
    return fw_dir


# ---------------------------------------------------------------------------
# 1. Happy validate_descriptor for each of the 3 descriptors
# ---------------------------------------------------------------------------


def test_validate_pytest_descriptor() -> None:
    """validate_descriptor roundtrips a valid pytest dict."""
    desc = validate_descriptor(_PYTEST_DICT)
    assert desc.name == "pytest"
    assert desc.language == "python"
    assert Lane.UNIT in desc.lanes
    assert desc.coverage_strategy == "cobertura"
    assert "requirements.txt:pytest" in desc.manifest_signals
    assert "tests/**/test_*.py" in desc.test_path_conventions
    assert desc.runtime.image == "tfactory-runner-pytest:latest"
    assert isinstance(desc.runtime.entrypoint, tuple)


def test_validate_jest_descriptor() -> None:
    """validate_descriptor roundtrips a valid Jest dict."""
    desc = validate_descriptor(_JEST_DICT)
    assert desc.name == "jest"
    assert desc.language == "typescript"
    assert Lane.UNIT in desc.lanes
    assert desc.coverage_strategy == "lcov"
    assert "**/*.test.ts" in desc.test_path_conventions


def test_validate_playwright_descriptor() -> None:
    """validate_descriptor roundtrips a valid Playwright dict."""
    desc = validate_descriptor(_PLAYWRIGHT_DICT)
    assert desc.name == "playwright"
    assert Lane.BROWSER in desc.lanes
    assert desc.coverage_strategy == "skip"
    assert "tests/e2e/**/*.spec.ts" in desc.test_path_conventions


# ---------------------------------------------------------------------------
# 2. Missing-field rejection for every required field
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "name",
    "language",
    "lanes",
    "version_range",
    "runtime",
    "manifest_signals",
    "test_path_conventions",
    "coverage_strategy",
    "context_block",
]


@pytest.mark.parametrize("missing_field", _REQUIRED_FIELDS)
def test_missing_required_field_raises(missing_field: str) -> None:
    """Each required field, when missing, raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    del data[missing_field]
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor(data)
    assert exc_info.value.field == missing_field
    assert "missing" in exc_info.value.reason.lower()


# ---------------------------------------------------------------------------
# 3. version_range parsing
# ---------------------------------------------------------------------------


def test_version_range_single_lower_bound() -> None:
    """>=7.0 parses correctly; min_version="7.0", max_version=None."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["version_range"] = ">=7.0"
    desc = validate_descriptor(data)
    assert desc.min_version == "7.0"
    assert desc.max_version is None


def test_version_range_two_bounds() -> None:
    """>=1.40,<2.0 gives min_version="1.40", max_version="2.0"."""
    desc = validate_descriptor(_PLAYWRIGHT_DICT)
    assert desc.min_version == "1.40"
    assert desc.max_version == "2.0"


def test_version_range_exact_version() -> None:
    """==7.4.0 parses without error; min_version=None (no >= operator)."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["version_range"] = "==7.4.0"
    desc = validate_descriptor(data)
    assert desc.version_range == "==7.4.0"
    assert desc.min_version is None
    assert desc.max_version is None


def test_version_range_invalid_raises() -> None:
    """A nonsense version_range raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["version_range"] = "not-a-version"
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor(data)
    assert exc_info.value.field == "version_range"


def test_specifier_set_membership() -> None:
    """specifier_set.contains() works correctly on a FrameworkDescriptor."""
    desc = validate_descriptor(_PLAYWRIGHT_DICT)  # >=1.40,<2.0
    assert "1.50.0" in desc.specifier_set
    assert "1.40.0" in desc.specifier_set
    assert "2.0.0" not in desc.specifier_set
    assert "1.39.0" not in desc.specifier_set


# ---------------------------------------------------------------------------
# 4. Unknown-lane rejection
# ---------------------------------------------------------------------------


def test_unknown_lane_raises() -> None:
    """An unrecognised lane name raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["lanes"] = ["sast"]  # v0.1 alias — now invalid (maps to unit with warning but not rejected)
    # sast IS a v0.1 alias, but it maps to UNIT with DeprecationWarning.
    # Use a truly unknown lane:
    data["lanes"] = ["does-not-exist-lane"]
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor(data)
    assert exc_info.value.field == "lanes"
    assert "does-not-exist-lane" in exc_info.value.reason


def test_v01_alias_lane_emits_deprecation_warning() -> None:
    """The v0.1 'functional' alias is accepted but emits DeprecationWarning."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["lanes"] = ["functional"]  # v0.1 alias → Lane.UNIT
    with pytest.warns(DeprecationWarning, match="deprecated v0.1 alias"):
        desc = validate_descriptor(data)
    assert Lane.UNIT in desc.lanes


def test_empty_lanes_list_raises() -> None:
    """An empty lanes list raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["lanes"] = []
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor(data)
    assert exc_info.value.field == "lanes"


# ---------------------------------------------------------------------------
# 5. Bad coverage_strategy rejection
# ---------------------------------------------------------------------------


def test_invalid_coverage_strategy_raises() -> None:
    """An invalid coverage_strategy value raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["coverage_strategy"] = "nonsense"
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor(data)
    assert exc_info.value.field == "coverage_strategy"
    assert "nonsense" in exc_info.value.reason


@pytest.mark.parametrize("strategy", ["lcov", "cobertura", "skip"])
def test_valid_coverage_strategies_accepted(strategy: str) -> None:
    """All three valid coverage_strategy values are accepted."""
    data = copy.deepcopy(_PYTEST_DICT)
    data["coverage_strategy"] = strategy
    desc = validate_descriptor(data)
    assert desc.coverage_strategy == strategy


# ---------------------------------------------------------------------------
# 6. Optional fields get empty tuple defaults
# ---------------------------------------------------------------------------


def test_templates_defaults_to_empty_tuple() -> None:
    """If 'templates' is absent, it defaults to an empty tuple."""
    data = copy.deepcopy(_PYTEST_DICT)
    data.pop("templates", None)
    desc = validate_descriptor(data)
    assert desc.templates == ()


def test_evaluator_hooks_defaults_to_empty_tuple() -> None:
    """If 'evaluator_hooks' is absent, it defaults to an empty tuple."""
    data = copy.deepcopy(_PYTEST_DICT)
    data.pop("evaluator_hooks", None)
    desc = validate_descriptor(data)
    assert desc.evaluator_hooks == ()


# ---------------------------------------------------------------------------
# 7. load_registry happy path
# ---------------------------------------------------------------------------


def test_load_registry_returns_all_three(tmp_path: Path) -> None:
    """load_registry returns a dict with all three keys."""
    fw_dir = _make_frameworks_dir(
        tmp_path,
        {"pytest": _PYTEST_DICT, "jest": _JEST_DICT, "playwright": _PLAYWRIGHT_DICT},
    )
    registry = load_registry(frameworks_dir=fw_dir)
    assert set(registry.keys()) == {"pytest", "jest", "playwright"}
    assert all(isinstance(v, FrameworkDescriptor) for v in registry.values())


def test_load_registry_empty_dir_returns_empty_dict(tmp_path: Path) -> None:
    """load_registry on an empty directory returns an empty dict."""
    fw_dir = tmp_path / "frameworks"
    fw_dir.mkdir()
    registry = load_registry(frameworks_dir=fw_dir)
    assert registry == {}


# ---------------------------------------------------------------------------
# 8. Duplicate-name detection
# ---------------------------------------------------------------------------


def test_load_registry_duplicate_name_raises(tmp_path: Path) -> None:
    """Two descriptors with the same name raise FrameworkRegistryError."""
    fw_dir = tmp_path / "frameworks"
    (fw_dir / "pytest-a").mkdir(parents=True)
    (fw_dir / "pytest-b").mkdir(parents=True)

    # Both claim name="pytest"
    (fw_dir / "pytest-a" / "descriptor.yaml").write_text(
        yaml.dump(_PYTEST_DICT), encoding="utf-8"
    )
    pytest_b = copy.deepcopy(_PYTEST_DICT)
    pytest_b["name"] = "pytest"  # Same name, different dir
    (fw_dir / "pytest-b" / "descriptor.yaml").write_text(
        yaml.dump(pytest_b), encoding="utf-8"
    )

    with pytest.raises(FrameworkRegistryError, match="duplicate"):
        load_registry(frameworks_dir=fw_dir)


# ---------------------------------------------------------------------------
# 9. Missing frameworks_dir error
# ---------------------------------------------------------------------------


def test_load_registry_missing_dir_raises(tmp_path: Path) -> None:
    """load_registry raises FrameworkRegistryError if dir doesn't exist."""
    nonexistent = tmp_path / "does_not_exist"
    with pytest.raises(FrameworkRegistryError, match="does not exist"):
        load_registry(frameworks_dir=nonexistent)


# ---------------------------------------------------------------------------
# 10. get_descriptor lookup + KeyError
# ---------------------------------------------------------------------------


def test_get_descriptor_returns_correct_entry(tmp_path: Path) -> None:
    """get_descriptor returns the right FrameworkDescriptor by name."""
    fw_dir = _make_frameworks_dir(
        tmp_path,
        {"pytest": _PYTEST_DICT, "jest": _JEST_DICT},
    )
    desc = get_descriptor("pytest", frameworks_dir=fw_dir)
    assert desc.name == "pytest"
    assert desc.language == "python"


def test_get_descriptor_raises_key_error_for_unknown(tmp_path: Path) -> None:
    """get_descriptor raises KeyError when the name is not found."""
    fw_dir = _make_frameworks_dir(tmp_path, {"pytest": _PYTEST_DICT})
    with pytest.raises(KeyError, match="vitest"):
        get_descriptor("vitest", frameworks_dir=fw_dir)


# ---------------------------------------------------------------------------
# 11. Frozen-dataclass immutability
# ---------------------------------------------------------------------------


def test_framework_descriptor_is_frozen() -> None:
    """Assigning to a frozen FrameworkDescriptor attribute raises FrozenInstanceError."""
    desc = validate_descriptor(_PYTEST_DICT)
    with pytest.raises((AttributeError, TypeError)):
        desc.name = "changed"  # type: ignore[misc]


def test_runtime_spec_is_frozen() -> None:
    """Assigning to a frozen RuntimeSpec attribute raises FrozenInstanceError."""
    spec = RuntimeSpec(image="img:latest", entrypoint=("cmd",))
    with pytest.raises((AttributeError, TypeError)):
        spec.image = "other"  # type: ignore[misc]


def test_framework_descriptor_is_hashable() -> None:
    """FrameworkDescriptor can be used as a dict key or set member."""
    desc = validate_descriptor(_PYTEST_DICT)
    s = {desc}
    assert desc in s
    d = {desc: "value"}
    assert d[desc] == "value"


# ---------------------------------------------------------------------------
# 12. runtime.entrypoint optional
# ---------------------------------------------------------------------------


def test_runtime_without_entrypoint_defaults_to_empty_tuple() -> None:
    """runtime.entrypoint is optional; defaults to () if absent."""
    data = copy.deepcopy(_PYTEST_DICT)
    del data["runtime"]["entrypoint"]
    desc = validate_descriptor(data)
    assert desc.runtime.entrypoint == ()


def test_runtime_missing_image_raises() -> None:
    """runtime.image is required; its absence raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    del data["runtime"]["image"]
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor(data)
    assert "runtime.image" in exc_info.value.field


# ---------------------------------------------------------------------------
# 13. Glob compilation catches malformed patterns
# ---------------------------------------------------------------------------


def test_malformed_glob_raises() -> None:
    """A glob pattern that compiles to invalid regex raises FrameworkDescriptorError."""
    data = copy.deepcopy(_PYTEST_DICT)
    # Use a pattern that fnmatch.translate produces but Python's re can't compile.
    # In practice fnmatch.translate produces valid regexes for any glob — so
    # test the validator path is reachable by injecting a known-bad regex instead.
    # The easiest way: patch fnmatch.translate to return a bad regex.
    import fnmatch
    from unittest.mock import patch

    with patch.object(fnmatch, "translate", return_value="[invalid(regex"):
        with pytest.raises(FrameworkDescriptorError) as exc_info:
            validate_descriptor(data)
        assert "test_path_conventions" in exc_info.value.field


# ---------------------------------------------------------------------------
# 14. non-dict root raises
# ---------------------------------------------------------------------------


def test_non_dict_root_raises() -> None:
    """validate_descriptor raises FrameworkDescriptorError when given a non-dict."""
    with pytest.raises(FrameworkDescriptorError) as exc_info:
        validate_descriptor("not a dict")  # type: ignore[arg-type]
    assert exc_info.value.field == "<root>"


def test_none_root_raises() -> None:
    """validate_descriptor raises FrameworkDescriptorError when given None."""
    with pytest.raises(FrameworkDescriptorError):
        validate_descriptor(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 15. End-to-end load of the REAL frameworks/ directory
# ---------------------------------------------------------------------------

# Repo root is 3 levels up from this test file:
#   tests/ → repo-root/
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_FRAMEWORKS_DIR = _REPO_ROOT / "frameworks"


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_frameworks_dir_loads_three_descriptors() -> None:
    """The real frameworks/ dir loads and contains exactly {pytest, jest, playwright}."""
    registry = load_registry(frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert "pytest" in registry, "pytest descriptor must exist"
    assert "jest" in registry, "jest descriptor must exist"
    assert "playwright" in registry, "playwright descriptor must exist"


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_pytest_descriptor() -> None:
    """The real pytest descriptor has correct values."""
    desc = get_descriptor("pytest", frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert desc.language == "python"
    assert Lane.UNIT in desc.lanes
    assert desc.coverage_strategy == "cobertura"
    assert any("test_*.py" in p for p in desc.test_path_conventions)
    assert any("pytest" in s for s in desc.manifest_signals)


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_playwright_descriptor() -> None:
    """The real Playwright descriptor has coverage_strategy=skip (Decision 11)."""
    desc = get_descriptor("playwright", frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert desc.language == "typescript"
    assert Lane.BROWSER in desc.lanes
    assert desc.coverage_strategy == "skip", (
        "Decision 11: browser lane must not emit coverage; "
        "Evaluator uses null, not zero"
    )
    assert any("spec.ts" in p for p in desc.test_path_conventions)


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_jest_descriptor() -> None:
    """The real Jest descriptor has correct values."""
    desc = get_descriptor("jest", frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert desc.language == "typescript"
    assert Lane.UNIT in desc.lanes
    assert desc.coverage_strategy == "lcov"
    assert any(".test.ts" in p for p in desc.test_path_conventions)


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_vitest_descriptor() -> None:
    """The real Vitest descriptor — Unit lane, lcov, jest-compatible (#110)."""
    desc = get_descriptor("vitest", frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert desc.language == "typescript"
    assert Lane.UNIT in desc.lanes
    assert desc.coverage_strategy == "lcov"
    assert any(".test.ts" in p for p in desc.test_path_conventions)
    assert any("vitest" in s for s in desc.manifest_signals)
    # Reuses the shared TypeScript Evaluator hooks (descriptor-only addition).
    assert any("lang_typescript" in h for h in desc.evaluator_hooks)
    assert len(desc.templates) == 5


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_cypress_descriptor() -> None:
    """The real Cypress descriptor — Browser lane, coverage=skip (#110, Decision 11)."""
    desc = get_descriptor("cypress", frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert desc.language == "typescript"
    assert Lane.BROWSER in desc.lanes
    assert desc.coverage_strategy == "skip", (
        "Decision 11: browser lane must not emit coverage; Evaluator uses null, not zero"
    )
    assert any(".cy.ts" in p for p in desc.test_path_conventions)
    assert any("cypress" in s for s in desc.manifest_signals)
    assert any("lang_typescript" in h for h in desc.evaluator_hooks)
    assert len(desc.templates) == 5


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_registry_has_five_frameworks() -> None:
    """The real registry now ships pytest/jest/playwright + vitest/cypress (#110)."""
    registry = load_registry(frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert {"pytest", "jest", "playwright", "vitest", "cypress"} <= set(registry)


@pytest.mark.skipif(
    not _REAL_FRAMEWORKS_DIR.is_dir(),
    reason=f"frameworks/ directory not found at {_REAL_FRAMEWORKS_DIR}",
)
def test_real_acceptance_criterion() -> None:
    """Task 1 acceptance criterion: load_registry returns dict with 'playwright'."""
    registry = load_registry(frameworks_dir=_REAL_FRAMEWORKS_DIR)
    assert "playwright" in registry
