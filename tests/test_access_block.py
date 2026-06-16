"""RFC-0007 (#84): attach_access wires the classifier into the contract emit."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.emit.access_block import attach_access  # noqa: E402


class _Target:
    """Duck-typed .pfactory.yml target (model_dump like a pydantic model)."""

    def __init__(self, d):
        self._d = d

    def model_dump(self, exclude_none=False):
        return dict(self._d)


class _Config:
    def __init__(self, targets):
        self.targets = targets


def test_no_config_is_noop():
    c = {"feature": "x"}
    assert attach_access(c, None) is c
    assert "access" not in c


def test_config_without_targets_omits_block():
    c = {}
    attach_access(c, _Config([]))
    assert "access" not in c  # discover_access returns None -> no block


def test_attaches_classified_requirements():
    cfg = _Config(
        [
            _Target(
                {
                    "name": "api",
                    "type": "http",
                    "auth": {"type": "bearer", "token_env": "T"},
                }
            ),
            _Target(
                {
                    "name": "web",
                    "type": "http",
                    "auth": {"type": "ref", "ref": "store:tc_1"},
                }
            ),
        ]
    )
    c = {}
    attach_access(c, cfg, spec_text="plain login")
    reqs = c["access"]["requirements"]
    by = {r["resource"]: r["auth_class"] for r in reqs}
    assert by == {"api": "A-machine-native", "web": "B-bootstrap-once"}


def test_interactive_mfa_threads_through_to_D():
    cfg = _Config(
        [
            _Target(
                {
                    "name": "web",
                    "type": "http",
                    "auth": {"type": "ref", "ref": "store:x"},
                }
            )
        ]
    )
    c = {}
    attach_access(c, cfg, spec_text="login requires a push notification approval")
    assert c["access"]["requirements"][0]["auth_class"] == "D-un-automatable"


def test_accepts_plain_dict_targets():
    cfg = _Config([{"name": "l", "type": "docker_compose"}])
    c = {}
    attach_access(c, cfg)
    assert c["access"]["requirements"][0]["auth_class"] == "C-ephemeral-target"
