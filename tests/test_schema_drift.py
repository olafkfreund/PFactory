"""Tests for the cross-repo schema-drift guard (RFC-0010 gap closure)."""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
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


# ── a gate that cannot run must fail, never pass (#440, Factory#433) ────


def _self_signed_https_server(tmp_path: Path) -> tuple[http.server.HTTPServer, int]:
    """A real local HTTPS server whose cert no client will trust."""
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    openssl = shutil.which("openssl")
    subprocess.run(  # noqa: S603 - fixed argv, resolved binary, test-only
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(crt), "-days", "1",
            "-subj", "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    srv = http.server.HTTPServer(
        ("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # don't offer TLSv1/1.1
    ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_certificate_failure_fails_the_gate(tmp_path):
    """A TLS cert failure is deterministic: exit 1, never a silent green skip.

    Driven through a real ``urlopen`` because the whole bug was the wrapping:
    the cert error arrives as ``URLError(reason=SSLCertVerificationError)``.
    """
    if not shutil.which("openssl"):
        pytest.skip("openssl not available")
    srv, port = _self_signed_https_server(tmp_path)
    try:
        rc = csd.main(["--canonical", f"https://localhost:{port}/schema.json"])
    finally:
        srv.shutdown()
    assert rc == 1


def test_is_transient_unwraps_urlopen_reason():
    cert_err = ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
    assert not csd.is_transient(urllib.error.URLError(cert_err))
    assert not csd.is_transient(cert_err)
    # A 404 on the pinned ref is deterministic too — reason is a str, not a cause.
    assert not csd.is_transient(
        urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    )
    # Genuinely transient causes may still soft-skip.
    assert csd.is_transient(urllib.error.URLError(TimeoutError("timed out")))
    assert csd.is_transient(urllib.error.URLError(socket.gaierror(-2, "no host")))


def test_soft_skip_is_loud(capsys):
    """A skip must be visibly distinguishable from a pass.

    This test does NOT reach the real CI job summary: the autouse
    ``_no_real_step_summary`` fixture in ``tests/conftest.py`` detaches
    ``$GITHUB_STEP_SUMMARY`` first. ``capsys`` catches the two stdout signals;
    it never caught the third, which is a file append (#457).
    """
    csd._warn_skipped(urllib.error.URLError(TimeoutError("timed out")))
    out = capsys.readouterr().out
    assert "SKIPPED" in out and "NOT VERIFIED" in out
    assert "::warning" in out  # GitHub annotation, visible on the checks page


def test_no_test_writes_to_the_real_step_summary(monkeypatch):
    """The suite must not be able to fake a 'gate skipped' notice on a green run.

    Guards the autouse fixture itself: with ``$GITHUB_STEP_SUMMARY`` set exactly
    as an Actions runner sets it, the fixture has already detached it by the
    time any test body runs, so ``_warn_skipped`` finds nothing to append to.
    """
    assert "GITHUB_STEP_SUMMARY" not in os.environ
    csd._warn_skipped(urllib.error.URLError(TimeoutError("timed out")))


def test_a_real_skip_still_reaches_the_job_summary(tmp_path, monkeypatch):
    """...and the notice a REAL skip writes is unaffected.

    The fix must not mute the warning, only stop tests forging it. A test that
    deliberately opts in — as the drift gate itself does on CI — still gets the
    full ``> [!WARNING]`` block.
    """
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    csd._warn_skipped(urllib.error.URLError(TimeoutError("timed out")))
    written = summary.read_text(encoding="utf-8")
    assert "> [!WARNING]" in written
    assert "SCHEMA DRIFT CHECK SKIPPED" in written and "NOT VERIFIED" in written


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
