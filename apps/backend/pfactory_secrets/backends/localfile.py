"""
``localfile`` backend — read a secret from a local file.

Supported formats (selected by the ref scheme, carried in ``ref.extra["format"]``):

- ``file:``   — plaintext file (whole contents, or a ``#field`` from a simple
  ``key: value`` / ``key=value`` file).
- ``sops:``   — a `sops`-encrypted file; decrypted via the ``sops`` CLI.
- ``age:``    — an `age`-encrypted file; decrypted via ``age``/``rage`` with an
  age identity.
- ``agenix:`` — an agenix secret, which is a plain age file (``<name>.age``);
  decrypted the same way as ``age:``.

All formats keep data on the local machine → egress class LOCAL.

Decryption shells out through the ``_run_decrypt`` seam so tests can exercise
the logic without real binaries or keys.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from pfactory_secrets import (
    BackendUnavailableError,
    EgressClass,
    SecretNotFoundError,
    SecretRef,
    SecretsBackend,
    SecretsError,
    SecretValue,
)

# age identity is discovered from (in order) an explicit env var or these
# conventional locations. Matches the sops-age + agenix conventions.
_AGE_IDENTITY_ENV = ("PFACTORY_AGE_IDENTITY", "SOPS_AGE_KEY_FILE", "AGE_IDENTITY_FILE")
_AGE_IDENTITY_DEFAULTS = (
    "~/.config/sops/age/keys.txt",
    "~/.config/agenix/identity.age",
    "~/.age/key.txt",
)
_DECRYPT_TIMEOUT = 30


class LocalFileBackend(SecretsBackend):
    name = "localfile"

    def available(self) -> bool:
        # Plaintext is always available; encrypted formats check their tool at
        # resolve time and raise BackendUnavailableError with guidance.
        return True

    def egress_class(self) -> EgressClass:
        return EgressClass.LOCAL

    def resolve(self, ref: SecretRef) -> SecretValue:
        fmt = ref.extra.get("format", "file")
        path = Path(ref.locator).expanduser()
        if not path.is_file():
            raise SecretNotFoundError(f"Secret file not found: {path}")

        if fmt == "file":
            plaintext = self._read_plaintext(path)
            source = f"file:{path}"
        elif fmt == "sops":
            plaintext = self._decrypt_sops(path)
            source = f"sops:{path}"
        elif fmt in ("age", "agenix"):
            plaintext = self._decrypt_age(path)
            source = f"{fmt}:{path}"
        else:  # pragma: no cover - refs.py only emits the formats above
            raise SecretsError(f"Unknown local-file format {fmt!r}")

        value = _select(plaintext, ref.field)
        if value is None:
            raise SecretNotFoundError(f"Field {ref.field!r} not found in {path}")
        return SecretValue(value=value, backend=self.name, ref=ref.raw, source=source)

    # -- format handlers ----------------------------------------------------

    @staticmethod
    def _read_plaintext(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BackendUnavailableError(f"Cannot read {path}: {exc}") from exc

    def _decrypt_sops(self, path: Path) -> str:
        binary = shutil.which("sops")
        if binary is None:
            raise BackendUnavailableError(
                "sops CLI not found — install sops to decrypt 'sops:' secrets."
            )
        return _run_decrypt([binary, "-d", str(path)], path)

    def _decrypt_age(self, path: Path) -> str:
        binary = shutil.which("age") or shutil.which("rage")
        if binary is None:
            raise BackendUnavailableError(
                "age/rage CLI not found — install age to decrypt 'age:'/'agenix:' secrets."
            )
        identity = _resolve_age_identity()
        if identity is None:
            raise BackendUnavailableError(
                "No age identity found. Set PFACTORY_AGE_IDENTITY (or "
                "SOPS_AGE_KEY_FILE) to your age key file, or place it at "
                "~/.config/sops/age/keys.txt."
            )
        return _run_decrypt(
            [binary, "-d", "-i", str(identity), str(path)], path
        )


# ---------------------------------------------------------------------------
# helpers (module-level so tests can monkeypatch the decryption seam)
# ---------------------------------------------------------------------------

def _run_decrypt(cmd: list[str], path: Path) -> str:
    """Run a decryption command and return stdout, or raise SecretsError."""
    try:
        proc = subprocess.run(  # noqa: S603 - cmd built from a resolved binary
            cmd,
            capture_output=True,
            text=True,
            timeout=_DECRYPT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendUnavailableError(f"Decryption of {path} failed to run: {exc}") from exc
    if proc.returncode != 0:
        raise SecretsError(
            f"Decryption of {path} failed (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:300]}"
        )
    return proc.stdout


def _resolve_age_identity() -> Path | None:
    for env in _AGE_IDENTITY_ENV:
        val = os.environ.get(env, "").strip()
        if val and Path(val).expanduser().is_file():
            return Path(val).expanduser()
    for default in _AGE_IDENTITY_DEFAULTS:
        p = Path(default).expanduser()
        if p.is_file():
            return p
    return None


def _select(raw: str, field: str | None) -> str | None:
    """Return the whole content (stripped) or a single ``key`` from a simple
    ``key: value`` / ``key=value`` file (flat keys only)."""
    if field is None:
        return raw.strip()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "="):
            if sep in line:
                key, _, val = line.partition(sep)
                if key.strip() == field:
                    return val.strip().strip("'\"")
                break
    return None


__all__ = ["LocalFileBackend"]
