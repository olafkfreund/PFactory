"""Tests for the handback correction-request builder + renderer (P2 / #184).

Pure-compute: canned dicts in, CorrectionRequest + markdown out. No network,
no AIFactory, no LLM — exactly the seam the live send (P4) will wrap.
"""

from __future__ import annotations

from agents.handback import build_correction_request, render_fix_request_md
from agents.handback.request import CorrectionRequest

# A representative source.json (post-P1) carrying the aifactory envelope.
SOURCE = {
    "project_id": "demo",
    "spec_id": "001-login",
    "branch": "feature/login",
    "base_ref": "main",
    "correction_cycle": 0,
    "aifactory": {
        "project_id": "demo",
        "spec_id": "001-login",
        "api_url": "http://localhost:3101",
        "task_id": "demo:001-login",
    },
}

VERDICTS = {
    "verdicts": [
        {"test_id": "t_ok", "verdict": "accept", "reasons": ["covers the AC"]},
        {"test_id": "t_flag", "verdict": "flag", "reasons": ["minor smell"]},
        {
            "test_id": "t_bad",
            "verdict": "reject",
            "reasons": ["assertion failed: expected 200, got 500"],
            "lane": "api",
            "acceptance_criterion": "login returns 200 on valid creds",
        },
    ]
}

TRIAGE = {
    "rejected": [{"test_id": "t_bad", "test_file": "tests/test_login_api.py"}],
    "committed": [{"test_id": "t_ok", "test_file": "tests/test_login_ok.py"}],
}


# ── build_correction_request ─────────────────────────────────────────────


def test_selects_only_rejects() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    ids = [f.test_id for f in req.failures]
    assert ids == ["t_bad"]  # accept + flag excluded


def test_enriches_file_from_triage_and_maps_fields() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    f = req.failures[0]
    assert f.test_file == "tests/test_login_api.py"  # pulled from triage bucket
    assert f.lane == "api"
    assert f.verdict == "reject"
    assert f.acceptance_criterion == "login returns 200 on valid creds"
    assert "expected 200, got 500" in f.reason


def test_task_id_from_envelope() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    assert req.aifactory_task_id == "demo:001-login"
    assert req.source_kind == "triage"


def test_task_id_derived_when_envelope_lacks_it() -> None:
    src = {"aifactory": {"project_id": "p", "spec_id": "s"}}
    req = build_correction_request({"verdicts": []}, None, src)
    assert req.aifactory_task_id == "p:s"


def test_all_accept_is_nothing_to_hand_back() -> None:
    verdicts = {"verdicts": [{"test_id": "a", "verdict": "accept"}]}
    req = build_correction_request(verdicts, None, SOURCE)
    assert req.failures == []
    assert req.nothing_to_hand_back is True


def test_visual_plan_makes_request_non_empty_even_without_failures() -> None:
    req = build_correction_request(
        {"verdicts": []}, None, SOURCE,
        visual_correction_plan="# Correction plan\n\nButton overlaps footer.",
    )
    assert req.failures == []
    assert req.nothing_to_hand_back is False
    assert req.source_kind == "visual_inspection"


def test_failures_sorted_by_test_id_for_determinism() -> None:
    verdicts = {
        "verdicts": [
            {"test_id": "z", "verdict": "reject", "reasons": ["x"]},
            {"test_id": "a", "verdict": "reject", "reasons": ["y"]},
        ]
    }
    req = build_correction_request(verdicts, None, SOURCE)
    assert [f.test_id for f in req.failures] == ["a", "z"]


def test_missing_reason_has_safe_fallback() -> None:
    verdicts = {"verdicts": [{"test_id": "t", "verdict": "reject"}]}
    req = build_correction_request(verdicts, None, SOURCE)
    assert req.failures[0].reason == "(no reason recorded)"


def test_to_dict_envelope_shape() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    d = req.to_dict()
    assert d["aifactory_task_id"] == "demo:001-login"
    assert d["aifactory"]["api_url"] == "http://localhost:3101"
    assert d["source"] == "triage"
    assert d["failing_tests"][0]["test_id"] == "t_bad"
    assert d["has_visual_plan"] is False


# ── render_fix_request_md ────────────────────────────────────────────────


def test_render_is_deterministic_and_complete() -> None:
    req = build_correction_request(VERDICTS, TRIAGE, SOURCE)
    md = render_fix_request_md(req)
    assert render_fix_request_md(req) == md  # deterministic
    assert "# QA Fix Request — from PFactory" in md
    assert "`demo:001-login`" in md
    assert "**Failing tests:** 1" in md
    assert "t_bad" in md
    assert "tests/test_login_api.py" in md
    assert "lane: api" in md
    assert "login returns 200 on valid creds" in md
    assert "expected 200, got 500" in md


def test_render_includes_visual_plan_section() -> None:
    req = build_correction_request(
        {"verdicts": []}, None, SOURCE,
        visual_correction_plan="Button overlaps the footer on mobile.",
    )
    md = render_fix_request_md(req)
    assert "## Visual inspection findings" in md
    assert "Button overlaps the footer on mobile." in md


def test_render_empty_request_still_well_formed() -> None:
    req = CorrectionRequest(aifactory=SOURCE["aifactory"])
    md = render_fix_request_md(req)
    assert "**Failing tests:** 0" in md
    assert "## Failures" not in md  # no failures section when there are none
