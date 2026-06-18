"""Tests for the cross-repo schema-drift guard (RFC-0010 gap closure)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SCRIPT = _ROOT / "scripts" / "check_schema_drift.py"

# Load the standalone script as a module.
_spec = importlib.util.spec_from_file_location("check_schema_drift", _SCRIPT)
csd = importlib.util.module_from_spec(_spec)
sys.modules["check_schema_drift"] = csd
_spec.loader.exec_module(csd)


# ── check_drift: directional subset (canonical ⊆ vendored) ──────────────


def test_no_drift_when_vendored_superset():
    canon = {"properties": {"a": {"type": "string"}}}
    vend = {"properties": {"a": {"type": "string"}, "extra": {}}}  # vendored may add
    assert csd.check_drift(canon, vend) == []


def test_missing_key_is_drift():
    canon = {"properties": {"change_mode": {"enum": ["migration"]}}}
    vend = {"properties": {}}
    problems = csd.check_drift(canon, vend)
    assert any("change_mode" in p for p in problems)


def test_enum_narrowing_is_drift():
    canon = {"lanes": ["unit", "equivalence"]}
    vend = {"lanes": ["unit"]}  # vendored missing a canonical value
    problems = csd.check_drift(canon, vend)
    assert any("equivalence" in p for p in problems)


def test_descriptions_ignored():
    canon = {"properties": {"a": {"description": "canonical text", "type": "string"}}}
    vend = {"properties": {"a": {"description": "different", "type": "string"}}}
    assert csd.check_drift(canon, vend) == []


def test_scalar_mismatch_is_drift():
    assert csd.check_drift({"type": "string"}, {"type": "integer"})


# ── the live vendored schema is in sync with the canonical hub copy ─────


def test_vendored_in_sync_with_local_hub():
    """When the Factory hub checkout is present, the vendored copy must match."""
    hub = _ROOT.parent / "Factory" / "apis" / "task-contract.schema.json"
    if not hub.is_file():
        pytest.skip("Factory hub checkout not available")
    canonical = json.loads(hub.read_text())
    vendored = json.loads(
        (
            _ROOT / "apps/backend/plan/emit/contracts/task-contract.schema.json"
        ).read_text()
    )
    assert csd.check_drift(canonical, vendored) == []
