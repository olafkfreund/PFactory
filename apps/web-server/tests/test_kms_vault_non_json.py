"""A non-JSON Vault Transit reply must not surface as a raw TypeError (#480).

Sibling of the ``pfactory_secrets`` Vault backend defect: hvac hands back the
raw ``requests.Response`` whenever the body is not JSON, and this module
subscripts the result directly (``resp["data"]["ciphertext"]``), which raises
``TypeError: 'Response' object is not subscriptable`` from inside the KMS layer
naming neither Vault nor the operation.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import requests

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.crypto.kms.vault import VaultTransitBackend  # noqa: E402

# Not a credential: the fake hvac client below ignores it entirely.
_PLACEHOLDER = "unused-by-the-fake-client"


class _FakeTransit:
    """Returns the raw Response, exactly as hvac does on a non-JSON body."""

    def __init__(self, resp: requests.Response) -> None:
        self._resp = resp

    def encrypt_data(self, **_kwargs: object) -> requests.Response:
        return self._resp

    def decrypt_data(self, **_kwargs: object) -> requests.Response:
        return self._resp


def _backend(monkeypatch: pytest.MonkeyPatch, status: int) -> VaultTransitBackend:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"<html>proxy error</html>"

    hvac = types.ModuleType("hvac")
    hvac.Client = lambda **_kwargs: types.SimpleNamespace(  # type: ignore[attr-defined]
        secrets=types.SimpleNamespace(transit=_FakeTransit(resp))
    )
    monkeypatch.setitem(sys.modules, "hvac", hvac)
    return VaultTransitBackend(addr="https://vault.internal:8200", token=_PLACEHOLDER)


@pytest.mark.parametrize("status", [204, 502])
def test_encrypt_non_json_response(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    with pytest.raises(RuntimeError, match="non-JSON response"):
        _backend(monkeypatch, status).encrypt(b"data-key")


@pytest.mark.parametrize("status", [204, 502])
def test_decrypt_non_json_response(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    with pytest.raises(RuntimeError, match="non-JSON response"):
        _backend(monkeypatch, status).decrypt(b"vault:v1:abc")
