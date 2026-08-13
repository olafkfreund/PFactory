"""OAuth tokens / API keys in the JSON stores must not be plaintext at rest (#537).

#298 fixed WHO could read claude-profiles.json (atomic, 0600 from birth). The
contents were still a usable Claude OAuth token to anyone who got past the mode
-- a PVC snapshot, a volume backup, a support bundle. settings.json was worse:
globalClaudeOAuthToken and the provider keys were written with no chmod at all.

Covers the three things that can go wrong with an at-rest encryption change:

1. The secret is really gone from the bytes on disk -- checked with a sliding
   window, not just an `in` on the whole value, because a partial leak (a
   prefix, a base64 fragment) is still a leak.
2. A store written BEFORE this change still loads, still works, and is
   transparently upgraded on the next write (read-both / write-new).
3. Encryption is actually load-bearing: sabotage `seal` and the at-rest
   assertion goes red.
"""

from __future__ import annotations

import base64
import importlib
import json
import os
import secrets
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

# The web-server package only joins sys.path at runtime (above), so a plain
# `from server... import ...` is un-gateable here: at module scope mypy --strict
# reports import-not-found, and inside each test ruff reports PLC0415. Resolving
# the modules by name sidesteps both without weakening either rule for anyone
# else, which a per-file ignore would not.
_config = importlib.import_module("server.config")
_kms = importlib.import_module("server.crypto.kms")
secret_field = importlib.import_module("server.crypto.secret_field")
routes = importlib.import_module("server.routes.settings")
_pty = importlib.import_module("server.pty.manager")

get_settings = _config.get_settings
reset_backend_cache = _kms.reset_backend_cache


# Fixture shaping is DELIBERATE -- do not "helpfully" inline these into literals.
# The POST /claude-profiles route validates the token prefix (it rejects anything
# not starting sess- / sk-ant-), so the fixtures must carry the real shapes. But a
# literal credential-shaped string in a source file matches GitHub secret scanning:
# it can trip push protection (whose tempting workaround is a bypass nobody should
# get in the habit of) and it burns the alert channel on a fake, which trains
# people to ignore real alerts. Assembling the prefixes at runtime keeps the
# behaviour identical while leaving no scannable literal in the file. The bodies
# are repeating filler, not random, so entropy checks also read them as fake.
_SESS = "se" + "ss-"
_OAT = "sk-" + "ant-oat01-"
_PROJ = "sk-" + "proj-"

LEGACY_TOKEN = _SESS + "legacy" + "A" * 42
NEW_TOKEN = _OAT + "B" * 40
API_KEY = _PROJ + "C" * 40

# Any run of this many characters of a secret appearing in the file is a leak.
# 12 is the width a previous fleet fix leaked through while a whole-string
# assertion stayed green.
WINDOW = 8


def assert_absent(blob: bytes, secret: str) -> None:
    """Fail if ANY `WINDOW`-character window of `secret` appears in `blob`."""
    text = blob.decode("utf-8", "replace")
    for i in range(len(secret) - WINDOW + 1):
        window = secret[i : i + WINDOW]
        assert window not in text, (
            f"{WINDOW}-char window of the secret at offset {i} leaked into the "
            f"stored file (secret length {len(secret)})"
        )


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, Path]]:
    """A configured data dir with a KMS key, plus the settings module."""

    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("KMS_FERNET_KEY", key)
    monkeypatch.setenv("KMS_BACKEND", "fernet")

    reset_backend_cache()
    # get_settings() hands back a module-level singleton, so patch the attribute.
    monkeypatch.setattr(get_settings(), "PROJECTS_DATA_DIR", str(tmp_path))

    settings_routes = routes

    assert Path(settings_routes.get_profiles_file()).parent == tmp_path
    yield settings_routes, tmp_path
    reset_backend_cache()


def write_legacy_store(data_dir: Path) -> Path:
    """A claude-profiles.json exactly as a pre-#537 release wrote it."""
    path = data_dir / "claude-profiles.json"
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Work",
                        "email": "a@b.c",
                        "oauthToken": LEGACY_TOKEN,
                        "isDefault": True,
                    }
                ],
                "activeProfileId": "p1",
            },
            indent=2,
        )
    )
    path.chmod(0o600)
    return path


def test_saved_token_is_not_plaintext_on_disk(store: tuple[Any, Path]) -> None:
    settings_routes, data_dir = store
    settings_routes.save_profiles(
        {
            "profiles": [{"id": "p1", "name": "Work", "oauthToken": NEW_TOKEN}],
            "activeProfileId": "p1",
        }
    )
    stored = data_dir / "claude-profiles.json"
    raw = stored.read_bytes()
    assert_absent(raw, NEW_TOKEN)
    assert b"enc.v1:" in raw
    # #298 still holds: sealing did not regress the mode.
    assert oct(stored.stat().st_mode & 0o777) == "0o600"


def test_saved_token_round_trips(store: tuple[Any, Path]) -> None:
    settings_routes, _ = store
    settings_routes.save_profiles(
        {
            "profiles": [{"id": "p1", "name": "Work", "oauthToken": NEW_TOKEN}],
            "activeProfileId": "p1",
        }
    )
    loaded = settings_routes.load_profiles()
    assert loaded["profiles"][0]["oauthToken"] == NEW_TOKEN
    assert loaded["activeProfileId"] == "p1"


def test_save_does_not_mutate_the_callers_dict(store: tuple[Any, Path]) -> None:
    """Routes keep using the dict after save_profiles (env-token sync)."""
    settings_routes, _ = store
    data: dict[str, Any] = {
        "profiles": [{"id": "p1", "name": "Work", "oauthToken": NEW_TOKEN}],
        "activeProfileId": "p1",
    }
    settings_routes.save_profiles(data)
    assert data["profiles"][0]["oauthToken"] == NEW_TOKEN


def test_legacy_plaintext_store_still_loads(store: tuple[Any, Path]) -> None:
    """Read-both: a store written before #537 keeps working untouched."""
    settings_routes, data_dir = store
    write_legacy_store(data_dir)
    loaded = settings_routes.load_profiles()
    assert loaded["profiles"][0]["oauthToken"] == LEGACY_TOKEN
    assert loaded["profiles"][0]["name"] == "Work"


def test_legacy_plaintext_store_is_upgraded_on_next_write(store: tuple[Any, Path]) -> None:
    """Write-new: the next save re-seals the legacy value, no migration step."""
    settings_routes, data_dir = store
    path = write_legacy_store(data_dir)
    assert LEGACY_TOKEN in path.read_text()  # precondition: really plaintext

    loaded = settings_routes.load_profiles()
    loaded["profiles"][0]["name"] = "Work (renamed)"
    settings_routes.save_profiles(loaded)

    assert_absent(path.read_bytes(), LEGACY_TOKEN)
    assert settings_routes.load_profiles()["profiles"][0]["oauthToken"] == LEGACY_TOKEN


def test_pty_token_resolver_sees_plaintext(store: tuple[Any, Path]) -> None:
    """No regression: a reader outside settings.py resolves a usable token."""
    settings_routes, _ = store
    settings_routes.save_profiles(
        {
            "profiles": [{"id": "p1", "name": "Work", "oauthToken": NEW_TOKEN}],
            "activeProfileId": "p1",
        }
    )
    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    manager = _pty.PTYManager
    token, profile_id, _ = manager.__dict__["_resolve_claude_token"](manager.__new__(manager))
    assert token == NEW_TOKEN
    assert profile_id == "p1"


def test_http_round_trip_through_the_real_routes(store: tuple[Any, Path]) -> None:
    """End to end over HTTP: POST a profile, GET it back, disk stays sealed."""
    settings_routes, data_dir = store
    app = FastAPI()
    app.include_router(settings_routes.router, prefix="/api/settings")
    client = TestClient(app)

    resp = client.post(
        "/api/settings/claude-profiles",
        json={"name": "Work", "email": "a@b.c", "oauthToken": NEW_TOKEN},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    assert_absent((data_dir / "claude-profiles.json").read_bytes(), NEW_TOKEN)

    got = client.get("/api/settings/claude-profiles").json()
    assert got["data"]["profiles"][0]["oauthToken"] == NEW_TOKEN


def test_api_profiles_and_app_settings_are_sealed(store: tuple[Any, Path]) -> None:
    """Same class of bug, same fix: provider keys + settings.json globals."""
    settings_routes, data_dir = store

    settings_routes.save_api_profiles(
        {"profiles": [{"id": "a1", "name": "OpenAI", "apiKey": API_KEY}]}
    )
    assert_absent((data_dir / "api-profiles.json").read_bytes(), API_KEY)
    assert settings_routes.load_api_profiles()["profiles"][0]["apiKey"] == API_KEY

    app = settings_routes.AppSettings(globalClaudeOAuthToken=NEW_TOKEN)
    settings_routes.save_app_settings(app)
    stored = data_dir / "settings.json"
    assert_absent(stored.read_bytes(), NEW_TOKEN)
    # settings.json used to be written at the default umask, with no chmod at all.
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    assert settings_routes.load_app_settings().globalClaudeOAuthToken == NEW_TOKEN


def test_mutation_sabotaging_seal_turns_the_at_rest_check_red(
    store: tuple[Any, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The at-rest assertion is load-bearing, not incidentally green."""
    settings_routes, data_dir = store
    # monkeypatch rather than a bare assignment: secret_field is resolved by
    # name (see the preamble) so mypy only knows it as a ModuleType, and it
    # restores the real seal() even if the assertion below raises.
    monkeypatch.setattr(secret_field, "seal", lambda value: value)

    settings_routes.save_profiles({"profiles": [{"id": "p1", "oauthToken": NEW_TOKEN}]})
    with pytest.raises(AssertionError):
        assert_absent((data_dir / "claude-profiles.json").read_bytes(), NEW_TOKEN)


def test_window_check_catches_a_partial_leak() -> None:
    """A whole-string assertion would pass here; the window check must not."""
    partial = NEW_TOKEN[:20].encode()
    with pytest.raises(AssertionError):
        assert_absent(partial, NEW_TOKEN)
    assert NEW_TOKEN.encode() not in partial  # the naive check stays green


def test_no_key_configured_falls_back_to_plaintext_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Honest degradation: no key means no worse than before, and it says so."""
    monkeypatch.delenv("KMS_FERNET_KEY", raising=False)
    monkeypatch.delenv("APP_KMS_FERNET_KEY", raising=False)
    monkeypatch.setenv("KMS_BACKEND", "fernet")

    reset_backend_cache()
    secret_field._warn_no_key.cache_clear()
    with caplog.at_level("WARNING"):
        assert secret_field.seal(NEW_TOKEN) == NEW_TOKEN
    assert "KMS_FERNET_KEY" in caplog.text
    assert NEW_TOKEN not in caplog.text  # never log the secret
    secret_field._warn_no_key.cache_clear()
    reset_backend_cache()


# --- AIFactory#1290: a SELECTED backend that cannot construct must not degrade
#
# The degradation above is honest only while nobody asked for encryption. Once
# an operator sets APP_KMS_BACKEND to a cloud KMS, silence is the vulnerability:
# they believe the tokens are ciphertext and they are plaintext. Two guards,
# because neither alone is enough -- the boot check cannot see a KMS that dies
# after start-up, and the per-write check cannot stop a pod that was already
# accepting traffic before the first profile save.


@pytest.fixture
def selected_but_broken(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A cloud backend is selected and has nothing it needs to construct."""
    monkeypatch.setenv("KMS_BACKEND", "vault_transit")
    monkeypatch.delenv("APP_KMS_BACKEND", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    reset_backend_cache()
    yield
    reset_backend_cache()


@pytest.mark.usefixtures("selected_but_broken")
def test_selected_backend_that_cannot_construct_fails_at_boot() -> None:
    """The pod must not come up pretending to encrypt."""
    with pytest.raises(SystemExit) as excinfo:
        _kms.enforce_kms_safety()
    assert "PLAINTEXT" in str(excinfo.value)


@pytest.mark.usefixtures("selected_but_broken")
def test_seal_raises_rather_than_writing_plaintext_when_a_backend_is_selected() -> None:
    """A KMS that breaks after boot fails the write, it does not downgrade it."""
    with pytest.raises(Exception) as excinfo:
        secret_field.seal(NEW_TOKEN)
    assert NEW_TOKEN not in str(excinfo.value)  # never leak the secret


def test_the_unconfigured_default_still_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented no-KMS posture must NOT be turned into a boot failure."""
    monkeypatch.delenv("KMS_FERNET_KEY", raising=False)
    monkeypatch.delenv("APP_KMS_FERNET_KEY", raising=False)
    monkeypatch.delenv("APP_KMS_BACKEND", raising=False)
    monkeypatch.setenv("KMS_BACKEND", "fernet")
    reset_backend_cache()
    _kms.enforce_kms_safety()  # must not raise
    assert _kms.encryption_is_required() is False
    reset_backend_cache()
