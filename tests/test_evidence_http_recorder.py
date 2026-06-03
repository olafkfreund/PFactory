"""Tests for the HTTP HAR recorder — Task 16 / #32 sub-task 16.3.

Covered:
  - HAR document shape (version, creator, entries)
  - urllib.request.urlopen interception (mocked — no network)
  - Empty case: no HTTP calls → empty entries array
  - Multiple calls produce multiple entries
  - Original urlopen is restored after context exit
  - HAR file written to correct location
  - Network.har is valid JSON
  - Entry fields: url, method, status, startedDateTime, time
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from agents.evidence.http_recorder import _build_har_document, record_http_to_har

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_response(
    url: str = "http://localhost/test",
    status: int = 200,
    reason: str = "OK",
    body: bytes = b'{"ok": true}',
    content_type: str = "application/json",
) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.reason = reason
    resp.url = url
    resp.read.return_value = body

    # Build a headers object that behaves like http.client.HTTPMessage
    headers_mock = MagicMock()
    headers_mock.items.return_value = [("Content-Type", content_type)]
    resp.headers = headers_mock
    return resp


# ─── _build_har_document ─────────────────────────────────────────────────────


def test_build_har_document_structure() -> None:
    doc = _build_har_document([])
    assert "log" in doc
    log = doc["log"]
    assert log["version"] == "1.2"
    assert "creator" in log
    assert log["creator"]["name"] == "PFactory HTTP Recorder"
    assert isinstance(log["entries"], list)


def test_build_har_document_empty_entries() -> None:
    doc = _build_har_document([])
    assert doc["log"]["entries"] == []


def test_build_har_document_preserves_entries() -> None:
    entry = {"startedDateTime": "2026-01-01T00:00:00+00:00", "time": 10.0,
             "request": {}, "response": {}, "timings": {}}
    doc = _build_har_document([entry])
    assert len(doc["log"]["entries"]) == 1


# ─── record_http_to_har — empty case ─────────────────────────────────────────


def test_record_http_to_har_empty_creates_har_file(tmp_path: Path) -> None:
    """No HTTP calls → .har file still written with empty entries."""
    with record_http_to_har(tmp_path, "t1"):
        pass  # no HTTP calls

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    assert har_path.exists(), "network.har should be written even with no calls"


def test_record_http_to_har_empty_entries_array(tmp_path: Path) -> None:
    with record_http_to_har(tmp_path, "t1"):
        pass

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    doc = json.loads(har_path.read_text())
    assert doc["log"]["entries"] == []


# ─── record_http_to_har — urllib interception ────────────────────────────────


def test_record_http_to_har_intercepts_urllib(tmp_path: Path) -> None:
    """urllib.request.urlopen is patched during context and records the call."""
    mock_resp = _make_mock_response(url="http://localhost:8080/health")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with record_http_to_har(tmp_path, "t1"):
            urllib.request.urlopen("http://localhost:8080/health")

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    doc = json.loads(har_path.read_text())
    entries = doc["log"]["entries"]
    assert len(entries) == 1


def test_record_http_to_har_entry_has_required_fields(tmp_path: Path) -> None:
    mock_resp = _make_mock_response(status=200)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with record_http_to_har(tmp_path, "t1"):
            urllib.request.urlopen("http://example.com/")

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    doc = json.loads(har_path.read_text())
    entry = doc["log"]["entries"][0]

    assert "startedDateTime" in entry
    assert "time" in entry
    assert "request" in entry
    assert "response" in entry
    assert "timings" in entry


def test_record_http_to_har_entry_response_status(tmp_path: Path) -> None:
    mock_resp = _make_mock_response(status=404, reason="Not Found")

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with record_http_to_har(tmp_path, "t1"):
            urllib.request.urlopen("http://localhost/missing")

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    doc = json.loads(har_path.read_text())
    entry = doc["log"]["entries"][0]
    assert entry["response"]["status"] == 404


def test_record_http_to_har_multiple_calls(tmp_path: Path) -> None:
    mock_resp1 = _make_mock_response(url="http://localhost/a")
    mock_resp2 = _make_mock_response(url="http://localhost/b")

    call_count = [0]

    def side_effect(url, *args, **kwargs):
        i = call_count[0]
        call_count[0] += 1
        return [mock_resp1, mock_resp2][i]

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with record_http_to_har(tmp_path, "t1"):
            urllib.request.urlopen("http://localhost/a")
            urllib.request.urlopen("http://localhost/b")

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    doc = json.loads(har_path.read_text())
    assert len(doc["log"]["entries"]) == 2


# ─── record_http_to_har — restore original ───────────────────────────────────


def test_record_http_to_har_restores_urlopen(tmp_path: Path) -> None:
    """Original urllib.request.urlopen is restored after context exit."""
    original = urllib.request.urlopen

    with record_http_to_har(tmp_path, "t1"):
        # During context: urlopen is patched
        assert urllib.request.urlopen is not original

    # After context: urlopen is restored
    assert urllib.request.urlopen is original


def test_record_http_to_har_restores_urlopen_on_exception(tmp_path: Path) -> None:
    """Original urlopen is restored even if an exception is raised."""
    original = urllib.request.urlopen

    with pytest.raises(RuntimeError):
        with record_http_to_har(tmp_path, "t1"):
            raise RuntimeError("intentional")

    assert urllib.request.urlopen is original


# ─── record_http_to_har — file location ──────────────────────────────────────


def test_record_http_to_har_file_location(tmp_path: Path) -> None:
    with record_http_to_har(tmp_path, "my-test-id"):
        pass

    expected = tmp_path / "findings" / "evidence" / "my-test-id" / "network.har"
    assert expected.exists()


def test_record_http_to_har_creates_parent_dirs(tmp_path: Path) -> None:
    """evidence directory is created if it doesn't exist."""
    spec_dir = tmp_path / "deep" / "nested" / "spec"
    # spec_dir does not exist yet
    with record_http_to_har(spec_dir, "t1"):
        pass

    har_path = spec_dir / "findings" / "evidence" / "t1" / "network.har"
    assert har_path.exists()


def test_record_http_to_har_valid_json(tmp_path: Path) -> None:
    with record_http_to_har(tmp_path, "t1"):
        pass

    har_path = tmp_path / "findings" / "evidence" / "t1" / "network.har"
    # Should not raise
    doc = json.loads(har_path.read_text())
    assert isinstance(doc, dict)
