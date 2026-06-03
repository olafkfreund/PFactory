"""Tests for Visual Inspection P1 — model + report + packager (#170 / #171)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from agents.visual_inspection import (
    RunMeta,
    StepResult,
    build_meta,
    new_run_id,
    package_run,
    render_inspection_report,
    verdict_for,
)

_NOW = datetime.datetime(2026, 6, 3, 13, 5, 0, tzinfo=datetime.timezone.utc)
_TARGET = {"name": "snow", "platform": "servicenow", "base_url": "https://acme.service-now.com"}


def _steps(fail: bool) -> list[StepResult]:
    s = [
        StepResult(1, "login", "pass", screenshot="01-login-pass.png"),
        StepResult(2, "open incident", "pass", screenshot="02-open-incident-pass.png"),
    ]
    if fail:
        s.append(StepResult(3, "submit", "fail", screenshot="03-submit-fail.png",
                            error="expected toast 'Saved' — got 'Required field'"))
    else:
        s.append(StepResult(3, "submit", "pass", screenshot="03-submit-pass.png"))
    return s


# ── model ────────────────────────────────────────────────────────────────────


def test_verdict_for() -> None:
    assert verdict_for(_steps(fail=False)) == "pass"
    assert verdict_for(_steps(fail=True)) == "fail"
    assert verdict_for([]) == "attention"


def test_new_run_id_sortable_and_slugged() -> None:
    assert new_run_id("ServiceNow Prod", now=_NOW) == "servicenow-prod-20260603130500"


def test_build_meta_counts_and_verdict() -> None:
    meta = build_meta(run_id="snow-x", target=_TARGET, steps=_steps(fail=True),
                      created_at=_NOW.isoformat(), video="video.webm", trace="trace.zip")
    d = meta.to_dict()
    assert d["verdict"] == "fail"
    assert d["counts"] == {"steps": 3, "passed": 2, "failed": 1}
    assert d["recording"] == {"video": "video.webm", "trace": "trace.zip"}
    assert d["steps"][2]["error"].startswith("expected toast")


# ── report ───────────────────────────────────────────────────────────────────


def test_report_renders_verdict_steps_and_problems() -> None:
    meta = build_meta(run_id="snow-x", target=_TARGET, steps=_steps(fail=True),
                      created_at=_NOW.isoformat())
    md = render_inspection_report(meta)
    assert "🔴 FAIL" in md and "2/3 steps passed" in md
    assert "| 1 | login |" in md and "![login](01-login-pass.png)" in md
    assert "## Problems found" in md
    assert "Step 3 — submit" in md
    assert "expected toast" in md  # error annotated
    assert "show-trace" in md  # replay hint


def test_report_clean_run_says_none() -> None:
    meta = build_meta(run_id="snow-x", target=_TARGET, steps=_steps(fail=False),
                      created_at=_NOW.isoformat())
    md = render_inspection_report(meta)
    assert "✅ PASS" in md
    assert "None — every verification step passed" in md


def test_report_is_byte_stable() -> None:
    meta = build_meta(run_id="snow-x", target=_TARGET, steps=_steps(fail=True),
                      created_at=_NOW.isoformat())
    assert render_inspection_report(meta) == render_inspection_report(meta)


# ── packager ─────────────────────────────────────────────────────────────────


def _evidence(tmp: Path) -> Path:
    ev = tmp / "evidence"
    ev.mkdir()
    for name in ("01-login-pass.png", "02-open-incident-pass.png", "03-submit-fail.png"):
        (ev / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode())
    (ev / "video.webm").write_bytes(b"VIDEO")
    (ev / "trace.zip").write_bytes(b"PK\x03\x04TRACE")
    return ev


def test_package_run_assembles_the_folder(tmp_path) -> None:
    ev = _evidence(tmp_path)
    meta = build_meta(run_id="snow-20260603130500", target=_TARGET, steps=_steps(fail=True),
                      created_at=_NOW.isoformat(), video="video.webm", trace="trace.zip")
    out = package_run(tmp_path / "automated-test", meta=meta, evidence_dir=ev)

    assert out.run_dir == tmp_path / "automated-test" / "snow-20260603130500"
    # screenshots copied + renamed by the convention
    assert (out.run_dir / "screenshots" / "03-submit-fail.png").is_file()
    # recording copied
    assert (out.run_dir / "recording" / "video.webm").read_bytes() == b"VIDEO"
    assert (out.run_dir / "recording" / "trace.zip").is_file()
    # report + meta written
    assert out.report_md.is_file() and out.meta_json.is_file()

    m = json.loads(out.meta_json.read_text())
    assert m["verdict"] == "fail"
    # meta paths are run-relative (not the raw evidence names)
    assert m["steps"][2]["screenshot"] == "screenshots/03-submit-fail.png"
    assert m["recording"]["video"] == "recording/video.webm"
    # the report references the run-relative screenshot path
    assert "screenshots/03-submit-fail.png" in out.report_md.read_text()


def test_package_run_tolerates_missing_artifacts(tmp_path) -> None:
    ev = tmp_path / "evidence"
    ev.mkdir()  # empty — no screenshots, no recording
    meta = build_meta(run_id="snow-x", target=_TARGET, steps=_steps(fail=False),
                      created_at=_NOW.isoformat(), video="video.webm")
    out = package_run(tmp_path / "automated-test", meta=meta, evidence_dir=ev)
    # still writes report + meta; missing screenshots become None
    assert out.report_md.is_file()
    m = json.loads(out.meta_json.read_text())
    # a missing screenshot is omitted from meta (clean json), not null
    assert "screenshot" not in m["steps"][0]
    assert m["recording"]["video"] is None
