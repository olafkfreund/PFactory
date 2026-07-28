"""The legacy API_TOKEN comparison must be constant-time and never match empty.

Both assertions matter, and the second is the one with teeth:

1. A correct token still authenticates — the fix must not break the legacy path.
2. An EMPTY configured token must never authenticate. This is what discriminates
   the fix from the bug: `hmac.compare_digest` guarded by `if not configured`
   returns False, whereas the previous `token == settings.API_TOKEN` makes
   `"" == ""` TRUE and authenticates an empty token as the shared secret.

Timing itself is not asserted — a wall-clock timing test is flaky in CI and
proves little on a shared runner. The guard is that the code path uses
compare_digest and rejects empties; assertion 2 fails the moment someone
reverts to `==`.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server import auth as auth_mod  # noqa: E402


def _with_configured_token(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(auth_mod, "get_settings", lambda: SimpleNamespace(API_TOKEN=value))


def test_correct_token_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_configured_token(monkeypatch, "s3kr1t-shared-token")
    assert auth_mod._is_legacy_api_token("s3kr1t-shared-token") is True


@pytest.mark.parametrize("presented", ["wrong", "s3kr1t-shared-toke", ""])
def test_wrong_token_is_rejected(monkeypatch: pytest.MonkeyPatch, presented: str) -> None:
    _with_configured_token(monkeypatch, "s3kr1t-shared-token")
    assert auth_mod._is_legacy_api_token(presented) is False


@pytest.mark.parametrize("presented", ["", "anything"])
def test_empty_configured_token_never_authenticates(
    monkeypatch: pytest.MonkeyPatch, presented: str
) -> None:
    # THE DISCRIMINATING CASE. With `==`, `"" == ""` is True and an empty token
    # authenticates as the shared secret. Unreachable today because config.py
    # generates a token when none is set, but that is one config change away.
    _with_configured_token(monkeypatch, "")
    assert auth_mod._is_legacy_api_token(presented) is False


def test_uses_constant_time_comparison() -> None:
    # Locks the mechanism, not just the behaviour: a reimplementation with `==`
    # would pass every case above except the empty-configured one, and someone
    # "simplifying" this later should trip here first.
    src = inspect.getsource(auth_mod._is_legacy_api_token)
    assert "compare_digest" in src, "the legacy token compare must be constant-time"
