"""First-run setup must never print the API token value to stdout.

PFactory config.py printed ``Generated API token: {token}`` and
``Authorization: Bearer {token}`` on first boot with no ``APP_API_TOKEN``.
Those lines land in the pod log, journald and any CI job that boots the server —
anyone with ``kubectl logs`` gets the wildcard admin credential. AIFactory
already fixed this (#324 M1); this is the fork-drift port.

The assertions are on the captured stdout LINES, matched against the token VALUE
itself, so they cannot be satisfied by the path-only replacement message.

The same call site is also the Finding-A fix for PFactory: the token and the JWT
secret were written with ``write_text`` then ``chmod(0o600)``, leaving them at
the umask default (0644) while already holding the secret. The window tests
assert on the mode at the moment the file first holds the secret, not on the
final mode — a final-mode assertion passes against the buggy code.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

import pytest  # noqa: E402

from server import config as config_mod  # noqa: E402


@pytest.fixture
def permissive_umask():
    """umask 0, so a 0644-creating write is not masked into looking safe."""
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


def _generate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(config_mod, "get_data_file", lambda name: tmp_path / name)
    settings = config_mod.Settings.__new__(config_mod.Settings)
    return config_mod.Settings._get_or_generate_token(settings)


def test_first_run_never_prints_the_token_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _generate(tmp_path, monkeypatch)
    lines = capsys.readouterr().out.splitlines()

    leaked = [line for line in lines if token in line]
    assert not leaked, (
        f"the API token value was printed to stdout (pod log / journald / CI log): {leaked}"
    )


def test_first_run_still_tells_the_operator_where_the_token_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Usability half of the fix: the path must still be discoverable."""
    _generate(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    token_file = tmp_path / ".token"

    assert str(token_file) in out, out
    assert f"cat {token_file}" in out, out


class _ChmodSpy:
    """Records any file that is group/other-readable at the instant chmod runs.

    ``write_text`` + ``chmod(0o600)`` shows up here with the secret already on
    disk at 0644: that IS the readable window. A helper that creates the file
    0600 from birth never chmods a secret at all, so it records nothing.
    """

    def __init__(self) -> None:
        self.windows: list[tuple[str, str]] = []
        self._real = Path.chmod

    def __enter__(self) -> "_ChmodSpy":
        spy = self

        def chmod(self_path: Path, mode: int, **kw):  # type: ignore[no-untyped-def]
            try:
                st = self_path.stat()
            except OSError:
                return spy._real(self_path, mode, **kw)
            if stat.S_IMODE(st.st_mode) & 0o077:
                spy.windows.append((str(self_path), oct(stat.S_IMODE(st.st_mode))))
            return spy._real(self_path, mode, **kw)

        Path.chmod = chmod  # type: ignore[method-assign,assignment]
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        Path.chmod = self._real  # type: ignore[method-assign]


def test_token_and_jwt_secret_have_no_world_readable_window(
    tmp_path: Path, permissive_umask: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.py:188/:218 — Settings._get_or_generate_{token,jwt_secret}."""
    monkeypatch.setattr(config_mod, "get_data_file", lambda name: tmp_path / name)
    settings = config_mod.Settings.__new__(config_mod.Settings)

    with _ChmodSpy() as spy:
        token = config_mod.Settings._get_or_generate_token(settings)
        secret = config_mod.Settings._get_or_generate_jwt_secret(settings)

    assert not spy.windows, (
        "a secret file existed at a group/other-readable mode before chmod "
        f"narrowed it (the readable window): {spy.windows}"
    )
    for path, value in ((tmp_path / ".token", token), (tmp_path / ".jwt_secret", secret)):
        assert path.read_text().strip() == value
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_concurrent_reader_never_sees_a_truncated_secret(tmp_path: Path) -> None:
    """write_text truncates in place; os.replace publishes whole-old or whole-new."""
    import threading
    import time

    from server.paths import write_secret_file

    p = tmp_path / ".token"
    payload = "sk-ant-oat01-" + "A" * 4000
    write_secret_file(p, payload)

    torn: list[str] = []
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            write_secret_file(p, payload)

    def reader() -> None:
        while not stop.is_set():
            try:
                seen = p.read_text()
            except FileNotFoundError:
                torn.append("file vanished mid-write")
                return
            if seen != payload:
                torn.append(f"partial read: {len(seen)} of {len(payload)} bytes")
                return

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    time.sleep(2)
    stop.set()
    for t in threads:
        t.join(timeout=10)

    assert not torn, f"reader saw a torn secret file (data-loss path): {torn[:3]}"
