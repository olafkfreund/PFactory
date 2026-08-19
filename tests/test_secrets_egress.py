#!/usr/bin/env python3
"""
Egress gate, manifest, redaction, and CLI tests (epic #62, issue #8).
"""

import logging
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _write_yml(project: Path, body: str) -> None:
    (project / ".pfactory.yml").write_text(body)


# ── egress gate ─────────────────────────────────────────────────────────────

def test_egress_disabled_by_default(tmp_path, monkeypatch):
    from pfactory_secrets.egress import egress_enabled

    monkeypatch.delenv("PFACTORY_EGRESS_ENABLED", raising=False)
    assert egress_enabled(tmp_path) is False  # no .pfactory.yml
    _write_yml(tmp_path, "version: 1\ntargets: []\n")
    assert egress_enabled(tmp_path) is False  # egress block omitted -> off


def test_egress_enabled_via_yml(tmp_path, monkeypatch):
    from pfactory_secrets.egress import egress_enabled

    monkeypatch.delenv("PFACTORY_EGRESS_ENABLED", raising=False)
    _write_yml(tmp_path, "version: 1\ntargets: []\negress:\n  enabled: true\n")
    assert egress_enabled(tmp_path) is True


def test_egress_env_override(tmp_path, monkeypatch):
    from pfactory_secrets.egress import egress_enabled

    monkeypatch.setenv("PFACTORY_EGRESS_ENABLED", "1")
    assert egress_enabled(tmp_path) is True  # forced even without yml


# ── manifest ────────────────────────────────────────────────────────────────

def test_build_manifest_secret_free(tmp_path):
    from pfactory_secrets.egress import build_manifest
    from pfactory_yml.schema import CredentialEntry, EgressConfig, EgressDestination

    creds = {
        "gcp": CredentialEntry(ref="gcp-sm://proj/sa", **{"as": "GOOGLE_APPLICATION_CREDENTIALS"}, kind="file"),
        "staging": CredentialEntry(ref="env:STAGING_TOKEN", **{"as": "STAGING_TOKEN"}),
    }
    egress = EgressConfig(enabled=True, destinations=[
        EgressDestination(name="api", host="api.staging.example.com"),
    ])
    m = build_manifest(creds, egress)
    assert m.enabled and len(m.rows) == 2
    gcp_row = next(r for r in m.rows if r.name == "gcp")
    assert gcp_row.backend == "gcp_secret_manager" and gcp_row.egress_class == "managed_cloud"
    env_row = next(r for r in m.rows if r.name == "staging")
    assert env_row.egress_class == "local"  # env backend is LOCAL
    md = m.render_markdown()
    # secret-free: no values, just names/backends/destinations
    # The whole destination row, not a bare host substring: the loose check
    # passes even if the host is rendered without its declared name, which is
    # what makes the manifest readable.
    assert "- api → `api.staging.example.com`" in md
    assert "GOOGLE_APPLICATION_CREDENTIALS" in md
    assert "proj/sa" not in md  # the ref locator is not leaked into the table


def test_manifest_disabled_render():
    from pfactory_secrets.egress import build_manifest

    m = build_manifest(None, None)
    assert not m.enabled
    assert "disabled" in m.render_markdown().lower()


# ── redaction ───────────────────────────────────────────────────────────────

def test_redactor_value_based():
    from pfactory_secrets.redaction import Redactor

    r = Redactor()
    r.register("supersecretvalue")
    r.register("ab")  # too short, ignored
    out = r.redact("token=supersecretvalue and again supersecretvalue; ab stays")
    assert "supersecretvalue" not in out
    assert out.count("***") == 2
    assert "ab stays" in out  # short value not redacted


def test_redacting_filter_on_logger(caplog):
    from pfactory_secrets.redaction import RedactingFilter, Redactor

    r = Redactor()
    r.register("leakytoken123")
    logger = logging.getLogger("test.redact")
    logger.addFilter(RedactingFilter(r))
    with caplog.at_level(logging.INFO, logger="test.redact"):
        logger.info("using leakytoken123 to auth")
    assert "leakytoken123" not in caplog.text
    assert "***" in caplog.text


def test_scrub_patterns_masks_assignment():
    from pfactory_secrets.redaction import scrub_patterns

    text = 'api_key = "abcdefghijklmnopqrstuvwxyz0123456789"'
    out = scrub_patterns(text)
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in out


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_cli_audit_disabled(tmp_path, capsys, monkeypatch):
    from pfactory_secrets.cli import main

    monkeypatch.delenv("PFACTORY_EGRESS_ENABLED", raising=False)
    _write_yml(tmp_path, "version: 1\ntargets: []\n")
    rc = main(["audit", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "disabled" in out.lower()


def test_cli_audit_enabled_json(tmp_path, capsys, monkeypatch):
    import json

    from pfactory_secrets.cli import main

    monkeypatch.delenv("PFACTORY_EGRESS_ENABLED", raising=False)
    _write_yml(
        tmp_path,
        "version: 1\ntargets: []\n"
        "egress:\n  enabled: true\n  destinations:\n    - {name: api, host: api.x.com}\n"
        "credentials:\n  tok: {ref: 'env:STAGING_TOKEN', as: STAGING_TOKEN}\n",
    )
    rc = main(["audit", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["enabled"] is True
    assert data["credentials"][0]["name"] == "tok"


def test_cli_doctor(capsys):
    from pfactory_secrets.cli import main

    rc = main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0 and "env" in out and "vault" in out


def test_cli_resolve_redacts(capsys, monkeypatch):
    from pfactory_secrets.cli import main

    monkeypatch.setenv("CLI_SECRET", "donotprint")
    rc = main(["resolve", "env:CLI_SECRET"])
    out = capsys.readouterr().out
    assert rc == 0 and "donotprint" not in out and "chars>" in out
