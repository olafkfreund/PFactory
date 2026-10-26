"""Contract: the handback sender's POST is a valid AIFactory apply-correction (#928).

PFactory CI has no AIFactory checkout, so the contract is VENDORED byte-identical
from AIFactory @ 74b4b9b9bed20c5d492d3e8a7b5067b0deb4bc2a:

- ``tests/contracts/aifactory-handback-triage.schema.json``
  ← ``apps/backend/qa/handback_triage.schema.json``
- ``tests/contracts/aifactory_handback_contract.py`` ← ``apps/backend/qa/handback_contract.py``

Re-vendor both when AIFactory changes the contract. The request fields mirror
``ApplyCorrectionRequest`` (``apps/web-server/server/routes/execution.py``) at the same SHA.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from agents.handback import build_correction_request
from agents.handback.send import default_sender, send_correction

jsonschema = pytest.importorskip("jsonschema")

_CONTRACTS = Path(__file__).parent / "contracts"
_SCHEMA = json.loads((_CONTRACTS / "aifactory-handback-triage.schema.json").read_text("utf-8"))
_spec = importlib.util.spec_from_file_location(
    "aifactory_handback_contract", _CONTRACTS / "aifactory_handback_contract.py"
)
_contract = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _contract  # dataclasses resolve their module here
_spec.loader.exec_module(_contract)


def _source(api_url):
    # Snapshotter-shaped envelope; api_url is None when there is no target.
    return {
        "aifactory": {
            "project_id": "demo",
            "spec_id": "001",
            "api_url": api_url,
            "task_id": "demo:001",
        }
    }


_REJECT = {
    "verdicts": [{"test_id": "t_bad", "verdict": "reject", "reasons": ["boom"], "lane": "api"}]
}
_ACCEPT = {"verdicts": [{"test_id": "t_ok", "verdict": "accept"}]}


@pytest.mark.parametrize(
    "request_",
    [
        build_correction_request(
            _REJECT, {"rejected": [{"test_id": "t_bad", "test_file": "t.py"}]}, _source("http://af")
        ),
        build_correction_request(
            _ACCEPT, None, _source(None), visual_correction_plan="fix the button"
        ),
    ],
    ids=["triage", "visual_only"],
)
def test_send_payload_satisfies_apply_correction_contract(tmp_path, request_):
    captured = {}
    res = send_correction(
        request_, tmp_path, dry_run=False, confirm=True, sender_fn=captured.update
    )
    assert res.sent

    assert captured["confirm"] is True
    assert captured["fix_request_md"].strip()
    triage = captured["triage"]
    errors = [e.message for e in jsonschema.Draft202012Validator(_SCHEMA).iter_errors(triage)]
    assert errors == []
    verdict = _contract.validate_triage_report(triage)
    assert verdict.ok, verdict.errors


def test_default_sender_posts_triage(monkeypatch):
    sent = {}

    def fake_urlopen(req, **_kw):
        sent.update(json.loads(req.data))
        return io.BytesIO(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    triage = {"source": "triage", "failing_tests": [{"test_id": "t", "reason": "r"}]}
    default_sender(
        {
            "api_url": "http://af",
            "task_id": "x",
            "fix_request_md": "m",
            "confirm": True,
            "triage": triage,
        }
    )
    assert sent["triage"] == triage and sent["confirm"] is True
