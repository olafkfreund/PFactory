"""RFC-0007 (#84) activation: service loads the snapshotted .pfactory.yml + spec.

_load_access_inputs reads ``<workspaces>/{project}/specs/{spec}/context/`` and is
best-effort — a missing snapshot yields (None, "") so the contract omits the
access block. End-to-end: a snapshot dict flows through attach_access into a
contract access block.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.emit.access_block import attach_access  # noqa: E402
from plan.service import _load_access_inputs  # noqa: E402


def _seed(
    base: Path, project: str, spec: str, *, cfg: dict | None, spec_md: str | None
):
    ctx = base / project / "specs" / spec / "context"
    ctx.mkdir(parents=True)
    if cfg is not None:
        (ctx / "pfactory_yml.json").write_text(json.dumps(cfg))
    if spec_md is not None:
        (ctx / "aifactory_spec.md").write_text(spec_md)
    return ctx


def test_missing_snapshot_yields_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PFACTORY_WORKSPACES_DIR", str(tmp_path))
    assert _load_access_inputs("proj", "001-x") == (None, "")


def test_loads_config_and_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("PFACTORY_WORKSPACES_DIR", str(tmp_path))
    cfg = {
        "targets": [
            {
                "name": "api",
                "type": "http",
                "auth": {"type": "bearer", "token_env": "T"},
            }
        ]
    }
    _seed(tmp_path, "proj", "001-x", cfg=cfg, spec_md="login via push notification")
    loaded_cfg, spec_text = _load_access_inputs("proj", "001-x")
    assert loaded_cfg == cfg
    assert "push notification" in spec_text


def test_snapshot_flows_into_contract_access_block(tmp_path, monkeypatch):
    monkeypatch.setenv("PFACTORY_WORKSPACES_DIR", str(tmp_path))
    cfg = {
        "targets": [
            {
                "name": "api",
                "type": "http",
                "auth": {"type": "bearer", "token_env": "T"},
            },
            {
                "name": "web",
                "type": "http",
                "auth": {"type": "ref", "ref": "store:tc_1"},
            },
        ]
    }
    _seed(tmp_path, "proj", "001-x", cfg=cfg, spec_md="plain login")
    loaded_cfg, spec_text = _load_access_inputs("proj", "001-x")
    contract: dict = {}
    attach_access(contract, loaded_cfg, spec_text)
    classes = {
        r["resource"]: r["auth_class"] for r in contract["access"]["requirements"]
    }
    assert classes == {"api": "A-machine-native", "web": "B-bootstrap-once"}


def test_bad_json_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("PFACTORY_WORKSPACES_DIR", str(tmp_path))
    ctx = tmp_path / "proj" / "specs" / "001-x" / "context"
    ctx.mkdir(parents=True)
    (ctx / "pfactory_yml.json").write_text("{not json")
    assert _load_access_inputs("proj", "001-x") == (None, "")
