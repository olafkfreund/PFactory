"""Tests for Visual Inspection P5 — handover opt-in metadata (#170 / #175).

Covers ``write_visual_inspection_meta``: the seam ``task_create_and_run`` calls
when a handover enables a visual inspection. The full MCP handler is SDK-gated
(its suite skips without the SDK), so the threadable logic is unit-tested here.
"""

from __future__ import annotations

import json

from agents.tools_pkg.tools.task_control import write_visual_inspection_meta


def test_enabled_writes_metadata(tmp_path) -> None:
    spec = tmp_path / "specs" / "001"
    spec.mkdir(parents=True)
    out = write_visual_inspection_meta(
        spec, {"enabled": True, "target": "snow", "flow": "open + submit an incident"}
    )
    assert out is True
    m = json.loads((spec / "context" / "visual_inspection.json").read_text())
    assert m == {"enabled": True, "target": "snow", "flow": "open + submit an incident"}


def test_disabled_writes_nothing(tmp_path) -> None:
    spec = tmp_path / "specs" / "001"
    spec.mkdir(parents=True)
    assert write_visual_inspection_meta(spec, {"enabled": False, "target": "snow"}) is False
    assert not (spec / "context" / "visual_inspection.json").exists()


def test_none_and_empty_are_noops(tmp_path) -> None:
    spec = tmp_path / "specs" / "001"
    spec.mkdir(parents=True)
    assert write_visual_inspection_meta(spec, None) is False
    assert write_visual_inspection_meta(spec, {}) is False
    assert not (spec / "context").exists() or not (spec / "context" / "visual_inspection.json").exists()
