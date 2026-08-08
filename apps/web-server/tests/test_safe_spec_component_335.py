"""#335: the path-traversal barrier used across the route/service layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WS = Path(__file__).resolve().parents[1]
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

from server.services.git_utils import safe_spec_component  # noqa: E402


@pytest.mark.parametrize(
    "evil",
    [
        "../../etc/passwd",
        "..",
        ".",
        "/abs/path",
        "a/b",
        "a\\b",
        "with\x00null",
        "",
        "..%2f..",  # not decoded here, but the slash-y result must still be rejected if it slips through
    ],
)
def test_rejects_traversal(evil):
    with pytest.raises(ValueError):
        safe_spec_component(evil)


@pytest.mark.parametrize(
    "ok",
    ["001-feature-name", "spec_1", "logo.png", "abc123", "a.b-c_d"],
)
def test_accepts_legit_components_unchanged(ok):
    assert safe_spec_component(ok) == ok
