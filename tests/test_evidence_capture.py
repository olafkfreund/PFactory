"""Tests for evidence layout helpers + CatalogEntry evidence fields.

Sub-tasks 16.1, 16.2, 16.4 — Task 16 / #32.

Covered:
  - evidence_dir_for_test: path construction
  - evidence_urls_for_test: empty dir, screenshots list, video/trace/network,
    mixed layout, unknown extension excluded
  - render_playwright_config: template substitution
  - content_type_for_artifact: extension mapping
  - CatalogEntry round-trip with evidence fields (last_evidence_run_id,
    evidence_urls)
  - CatalogEntry backward-compat: old catalog without evidence fields
  - CatalogEntry to_dict omit-when-empty contract
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── Import the layout helpers directly (no FastAPI / extra deps needed) ──────
from agents.evidence.layout import (
    content_type_for_artifact,
    evidence_dir_for_test,
    evidence_urls_for_test,
    render_playwright_config,
)
from tests_catalog.schema import CatalogEntry

# ─── evidence_dir_for_test ────────────────────────────────────────────────────


def test_evidence_dir_for_test_path_structure(tmp_path: Path) -> None:
    ev_dir = evidence_dir_for_test(tmp_path, "ac1-login")
    assert ev_dir == tmp_path / "findings" / "evidence" / "ac1-login"


def test_evidence_dir_for_test_does_not_create_dir(tmp_path: Path) -> None:
    ev_dir = evidence_dir_for_test(tmp_path, "ac2-test")
    assert not ev_dir.exists()


def test_evidence_dir_for_test_nested_spec_dir(tmp_path: Path) -> None:
    spec_dir = tmp_path / "workspaces" / "proj" / "specs" / "my-spec"
    ev_dir = evidence_dir_for_test(spec_dir, "t1")
    assert ev_dir == spec_dir / "findings" / "evidence" / "t1"


# ─── evidence_urls_for_test ───────────────────────────────────────────────────


def test_evidence_urls_empty_dir(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    # ev_dir does not exist
    result = evidence_urls_for_test("spec-1", "t1", ev_dir)
    assert result == {}


def test_evidence_urls_nonexistent_dir(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    result = evidence_urls_for_test("spec-1", "t1", ev_dir)
    assert result == {}


def test_evidence_urls_screenshots_list(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    shots_dir = ev_dir / "screenshots"
    shots_dir.mkdir(parents=True)
    (shots_dir / "0001.png").write_bytes(b"png1")
    (shots_dir / "0002.png").write_bytes(b"png2")

    result = evidence_urls_for_test("my-spec", "t1", ev_dir)
    assert isinstance(result["screenshots"], list)
    assert len(result["screenshots"]) == 2
    for url in result["screenshots"]:
        assert url.startswith("/api/pfactory/tasks/my-spec/evidence/t1/screenshots/")
        assert url.endswith(".png")


def test_evidence_urls_screenshots_sorted(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    shots_dir = ev_dir / "screenshots"
    shots_dir.mkdir(parents=True)
    (shots_dir / "b.png").write_bytes(b"")
    (shots_dir / "a.png").write_bytes(b"")

    result = evidence_urls_for_test("s", "t1", ev_dir)
    names = [u.rsplit("/", 1)[-1] for u in result["screenshots"]]
    assert names == sorted(names)


def test_evidence_urls_video(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    ev_dir.mkdir(parents=True)
    (ev_dir / "video.webm").write_bytes(b"webm")

    result = evidence_urls_for_test("spec-1", "t1", ev_dir)
    assert result["video"] == "/api/pfactory/tasks/spec-1/evidence/t1/video.webm"


def test_evidence_urls_trace(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    ev_dir.mkdir(parents=True)
    (ev_dir / "trace.zip").write_bytes(b"zip")

    result = evidence_urls_for_test("spec-1", "t1", ev_dir)
    assert result["trace"] == "/api/pfactory/tasks/spec-1/evidence/t1/trace.zip"


def test_evidence_urls_network_har(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    ev_dir.mkdir(parents=True)
    (ev_dir / "network.har").write_bytes(b"{}")

    result = evidence_urls_for_test("spec-1", "t1", ev_dir)
    assert result["network"] == "/api/pfactory/tasks/spec-1/evidence/t1/network.har"


def test_evidence_urls_mixed_layout(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    ev_dir.mkdir(parents=True)
    shots_dir = ev_dir / "screenshots"
    shots_dir.mkdir()
    (shots_dir / "1.png").write_bytes(b"")
    (ev_dir / "video.webm").write_bytes(b"")
    (ev_dir / "trace.zip").write_bytes(b"")
    (ev_dir / "network.har").write_bytes(b"")

    result = evidence_urls_for_test("s", "t1", ev_dir)
    assert "screenshots" in result
    assert "video" in result
    assert "trace" in result
    assert "network" in result


def test_evidence_urls_unknown_extension_excluded(tmp_path: Path) -> None:
    ev_dir = tmp_path / "findings" / "evidence" / "t1"
    ev_dir.mkdir(parents=True)
    (ev_dir / "debug.log").write_bytes(b"log data")

    result = evidence_urls_for_test("s", "t1", ev_dir)
    # .log is not in _CONTENT_TYPE_MAP → excluded
    assert "debug" not in result
    assert "debug.log" not in result


# ─── render_playwright_config ─────────────────────────────────────────────────


def test_render_playwright_config_substitutes_output_dir(tmp_path: Path) -> None:
    output = tmp_path / "findings" / "evidence" / "t1"
    rendered = render_playwright_config(output, "http://localhost:3000")
    assert str(output) in rendered


def test_render_playwright_config_substitutes_base_url(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    rendered = render_playwright_config(output, "https://staging.example.com")
    assert "https://staging.example.com" in rendered


def test_render_playwright_config_default_policies(tmp_path: Path) -> None:
    rendered = render_playwright_config(tmp_path / "ev", "http://localhost")
    assert "only-on-failure" in rendered
    assert "retain-on-failure" in rendered
    assert "on-first-retry" in rendered


def test_render_playwright_config_custom_policies(tmp_path: Path) -> None:
    rendered = render_playwright_config(
        tmp_path / "ev",
        "http://localhost",
        screenshot_policy="always",
        video_policy="always",
        trace_policy="always",
    )
    assert rendered.count("always") >= 3


def test_render_playwright_config_no_placeholder_leakage(tmp_path: Path) -> None:
    rendered = render_playwright_config(tmp_path / "ev", "http://localhost")
    assert "@@" not in rendered


# ─── content_type_for_artifact ────────────────────────────────────────────────


@pytest.mark.parametrize("filename,expected", [
    ("screenshot.png", "image/png"),
    ("SCREENSHOT.PNG", "image/png"),
    ("video.webm", "video/webm"),
    ("trace.zip", "application/zip"),
    ("network.har", "application/json"),
    ("events.jsonl", "application/json"),
    ("clip.mp4", "video/mp4"),
    ("unknown.bin", "application/octet-stream"),
])
def test_content_type_for_artifact(filename: str, expected: str) -> None:
    assert content_type_for_artifact(filename) == expected


# ─── CatalogEntry with evidence fields ────────────────────────────────────────


def _base_entry_kwargs() -> dict:
    return {
        "test_id": "ac1-login",
        "test_file": "tests/e2e/login.spec.ts",
        "framework": "playwright",
        "lane": "browser",
        "language": "typescript",
        "covers_acs": ("AC#1",),
        "generated_at": "2026-05-29T10:00:00Z",
        "generated_by_task": "spec-001",
        "last_verdict": "accept",
    }


def _base_entry_kwargs_dict() -> dict:
    return {
        "test_id": "ac1-login",
        "test_file": "tests/e2e/login.spec.ts",
        "framework": "playwright",
        "lane": "browser",
        "language": "typescript",
        "covers_acs": ["AC#1"],
        "generated_at": "2026-05-29T10:00:00Z",
        "generated_by_task": "spec-001",
        "last_verdict": "accept",
    }


def _urls_to_raw(
    urls: dict[str, str | list[str]],
) -> tuple[tuple[str, str | tuple[str, ...]], ...]:
    """Convert a plain dict to the hashable evidence_urls_raw format."""
    return tuple(
        (k, tuple(v) if isinstance(v, list) else v)
        for k, v in urls.items()
    )


def test_catalog_entry_default_evidence_fields() -> None:
    entry = CatalogEntry(**_base_entry_kwargs())
    assert entry.last_evidence_run_id is None
    assert entry.evidence_urls == {}


def test_catalog_entry_with_evidence_fields() -> None:
    urls = {
        "screenshots": ["/api/pfactory/tasks/s1/evidence/t1/screenshots/0001.png"],
        "video": "/api/pfactory/tasks/s1/evidence/t1/video.webm",
    }
    entry = CatalogEntry(
        **_base_entry_kwargs(),
        last_evidence_run_id="run-abc123",
        evidence_urls_raw=_urls_to_raw(urls),
    )
    assert entry.last_evidence_run_id == "run-abc123"
    assert entry.evidence_urls == urls


def test_catalog_entry_round_trip_with_evidence() -> None:
    urls: dict[str, str | list[str]] = {
        "video": "/api/pfactory/tasks/s1/evidence/t1/video.webm",
        "trace": "/api/pfactory/tasks/s1/evidence/t1/trace.zip",
    }
    entry = CatalogEntry(
        **_base_entry_kwargs(),
        last_evidence_run_id="run-xyz",
        evidence_urls_raw=_urls_to_raw(urls),
    )
    d = entry.to_dict()
    restored = CatalogEntry.from_dict(d)
    assert restored.last_evidence_run_id == "run-xyz"
    assert restored.evidence_urls == urls


def test_catalog_entry_to_dict_omits_evidence_when_default() -> None:
    entry = CatalogEntry(**_base_entry_kwargs())
    d = entry.to_dict()
    assert "last_evidence_run_id" not in d
    assert "evidence_urls" not in d


def test_catalog_entry_to_dict_omits_empty_evidence_urls() -> None:
    # evidence_urls_raw=() is the default (empty)
    entry = CatalogEntry(**_base_entry_kwargs(), evidence_urls_raw=())
    d = entry.to_dict()
    assert "evidence_urls" not in d


def test_catalog_entry_to_dict_includes_run_id_when_set() -> None:
    entry = CatalogEntry(**_base_entry_kwargs(), last_evidence_run_id="r1")
    d = entry.to_dict()
    assert d["last_evidence_run_id"] == "r1"


def test_catalog_entry_from_dict_backward_compat_no_evidence_fields() -> None:
    """Old catalog files that lack evidence fields round-trip cleanly."""
    d = {
        "test_id": "ac1-login",
        "test_file": "tests/e2e/login.spec.ts",
        "framework": "playwright",
        "lane": "browser",
        "language": "typescript",
        "covers_acs": ["AC#1"],
        "generated_at": "2026-05-29T10:00:00Z",
        "generated_by_task": "spec-001",
        "last_verdict": "accept",
    }
    entry = CatalogEntry.from_dict(d)
    assert entry.last_evidence_run_id is None
    assert entry.evidence_urls == {}


def test_catalog_entry_from_dict_handles_null_evidence_run_id() -> None:
    d = {**_base_entry_kwargs_dict(), "last_evidence_run_id": None}
    entry = CatalogEntry.from_dict(d)
    assert entry.last_evidence_run_id is None


def test_catalog_entry_from_dict_handles_invalid_evidence_urls_type() -> None:
    """If evidence_urls is not a dict (malformed), treat as empty."""
    d = {**_base_entry_kwargs_dict(), "evidence_urls": "not-a-dict"}
    entry = CatalogEntry.from_dict(d)
    assert entry.evidence_urls == {}


def test_catalog_entry_is_hashable_with_evidence_fields() -> None:
    """CatalogEntry with evidence fields stays hashable (frozen dataclass)."""
    urls = {"video": "/api/pfactory/tasks/s1/evidence/t1/video.webm"}
    entry = CatalogEntry(
        **_base_entry_kwargs(),
        last_evidence_run_id="r1",
        evidence_urls_raw=_urls_to_raw(urls),
    )
    # Should not raise TypeError
    h = hash(entry)
    assert isinstance(h, int)
    # Can be used as dict key or in set
    s = {entry}
    assert entry in s
