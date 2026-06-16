"""RFC-0007 (#86 PR-a): credential existence probe + curation-state model."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from pfactory_secrets.probe import probe_ref_exists  # noqa: E402
from plan.access_discovery import curation_status  # noqa: E402

# ---- probe_ref_exists (presence only, never the value) --------------------- #


def test_env_presence_is_knowable(monkeypatch):
    monkeypatch.setenv("HAS_IT", "x")
    monkeypatch.delenv("NOT_SET", raising=False)
    assert probe_ref_exists("env:HAS_IT") is True
    assert probe_ref_exists("env:NOT_SET") is False


def test_store_resolves_via_injected_resolver_presence_only():
    assert probe_ref_exists("store:tc_1", resolver=lambda r: "secret-value") is True


def test_resolver_error_is_undeterminable_not_missing():
    def boom(_ref):
        raise RuntimeError("no vault backend")

    # absent vs no-backend must not be conflated -> None, never False.
    assert probe_ref_exists("vault:secret/data/x#k", resolver=boom) is None


def test_garbage_ref_is_none():
    assert probe_ref_exists(None) is None
    assert probe_ref_exists("no-scheme") is None


# ---- curation_status ------------------------------------------------------- #


def _req(**kw):
    base = {"resource": "r", "auth_class": "A-machine-native", "bootstrap": "none"}
    base.update(kw)
    return base


def test_states_combine_structure_and_presence():
    reqs = [
        _req(resource="curated", curated=True),
        _req(resource="mfa", auth_class="D-un-automatable", bootstrap="human"),
        _req(resource="present", credential_ref="store:ok"),
        _req(resource="missing", credential_ref="env:NOPE"),
        _req(resource="unverified", credential_ref="vault:x#k"),
        _req(resource="mounted"),  # class A, no ref -> ready
    ]
    exists = {"store:ok": True, "env:NOPE": False, "vault:x#k": None}
    res = curation_status(reqs, ref_exists=lambda r: exists.get(r))
    by = {s["resource"]: s["state"] for s in res["requirements"]}
    assert by == {
        "curated": "curated",
        "mfa": "un_automatable",
        "present": "ready",
        "missing": "missing_credential",
        "unverified": "unverified",
        "mounted": "ready",
    }
    assert res["ready"] is False  # not all ready/curated


def test_needs_bootstrap_when_human_and_no_ref():
    res = curation_status([_req(auth_class="B-bootstrap-once", bootstrap="human")])
    assert res["requirements"][0]["state"] == "needs_bootstrap"


def test_default_prober_is_unverified():
    # No injected prober -> refs are undeterminable here (deferred to curation).
    res = curation_status([_req(credential_ref="store:x")])
    assert res["requirements"][0]["state"] == "unverified"


def test_all_ready_is_ready():
    res = curation_status([_req(), _req(resource="c", curated=True)])
    assert res["ready"] is True
