"""CLI entry point for ``server.crypto``.

Subcommand::

    python -m server.crypto rotate-root --new-kms-key-id <audit-label>

The rotation walks every ``kms_data_keys`` row, decrypts the
``wrapped_key`` under the OLD root, and re-wraps it under the NEW root.
The plaintext data keys themselves don't change, so no application
data needs to be touched.

Configuring OLD vs NEW backends:
  - OLD = the current env (whatever ``APP_KMS_BACKEND`` currently
    selects, plus that backend's standard env vars).
  - NEW = the SAME backend type, but with the key-reference env var
    overlaid with a ``_NEW`` suffix. Concretely:

      | Backend       | OLD env (already set)   | NEW env (set before run) |
      | ------------- | ----------------------- | ------------------------ |
      | fernet        | KMS_FERNET_KEY          | KMS_FERNET_KEY_NEW       |
      | aws_kms       | AWS_KMS_KEY_ID          | AWS_KMS_KEY_ID_NEW       |
      | vault_transit | VAULT_TRANSIT_KEY       | VAULT_TRANSIT_KEY_NEW    |
      | azure_kv      | AZURE_KEYVAULT_KEY      | AZURE_KEYVAULT_KEY_NEW   |
      | gcp_kms       | GCP_KMS_KEY_NAME        | GCP_KMS_KEY_NAME_NEW     |

    The running identity must hold Encrypt+Decrypt on BOTH keys for
    the duration of the rotation (cloud backends) or both keys must
    be in the env (fernet). After rotation, operators flip the OLD
    env var to point at the new key and revoke the old.

Cross-backend rotation (e.g. fernet → aws_kms) is NOT supported by
the CLI. That's a higher-stakes ops migration with audit implications;
operators run a custom Python script using ``rotate_root()`` directly.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url

from .kms import get_backend, reset_backend_cache
from .rotation import rotate_root

# Per-backend env var that holds the key reference. The "_NEW" suffix
# is the convention for rotation.
_KEY_ENV_BY_BACKEND: dict[str, str] = {
    "fernet": "KMS_FERNET_KEY",
    "aws_kms": "AWS_KMS_KEY_ID",
    "vault_transit": "VAULT_TRANSIT_KEY",
    "azure_kv": "AZURE_KEYVAULT_KEY",
    "gcp_kms": "GCP_KMS_KEY_NAME",
}


def _sync_url(db_url: str) -> str:
    """The sync-driver URL for an async ``DATABASE_URL`` (#781).

    The driver is NAMED, never inferred. This used to strip ``+asyncpg`` and
    hand the bare ``postgresql://`` to ``create_engine``, which loads whatever
    SQLAlchemy defaults to -- psycopg2 through 2.0, psycopg 3 from 2.1. The
    image ships neither, so the documented rotation runbook died inside
    ``create_engine`` before touching a row, on every SQLAlchemy version.

    Naming the driver, rather than stripping, is what makes this work on both
    SQLAlchemy majors: measured, ``2.0.51`` + the old stripping still died on
    ``psycopg2``. A ``DATABASE_URL`` that itself names ``+psycopg2`` is NOT
    rescued here and deliberately so -- ``database/engine.py`` builds its async
    engine from the raw value at import time and dies on that shape first, so
    such a deployment has never booted at all. SQLite keeps the stdlib driver,
    so those deployments are untouched.
    """
    url = make_url(db_url)
    backend = url.get_backend_name()
    if backend == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif backend == "sqlite":
        url = url.set(drivername="sqlite")
    else:
        return db_url
    # hide_password=False is REQUIRED: str(url) renders the password as "***",
    # so the engine would try to connect with that literal (caught by this
    # module's own unit tests, which assert the password survives).
    return url.render_as_string(hide_password=False)


@contextmanager
def _env_overlay(overrides: dict[str, str]) -> Iterator[None]:
    """Temporarily replace env vars; restore on exit."""
    original: dict[str, str | None] = {k: os.environ.get(k) for k in overrides}
    for k, v in overrides.items():
        os.environ[k] = v
    try:
        yield
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _cmd_rotate_root(args: argparse.Namespace) -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set — required for rotation", file=sys.stderr)
        return 2

    backend_name = (
        (os.environ.get("APP_KMS_BACKEND") or os.environ.get("KMS_BACKEND") or "fernet")
        .strip()
        .lower()
    )

    key_env = _KEY_ENV_BY_BACKEND.get(backend_name)
    if key_env is None:
        print(
            f"unknown backend {backend_name!r}; supported: {sorted(_KEY_ENV_BY_BACKEND)}",
            file=sys.stderr,
        )
        return 2

    new_key_env = f"{key_env}_NEW"
    new_key_value = os.environ.get(new_key_env)
    if not new_key_value:
        print(
            f"{new_key_env} is not set. To rotate the {backend_name} backend, "
            f"export {new_key_env}=<new-key-reference> before invoking.",
            file=sys.stderr,
        )
        return 2

    try:
        sync_url = _sync_url(db_url)
    except Exception as exc:  # noqa: BLE001 - argparse-style message, not a traceback
        # The noqa is for the ratchet, not a style exception: this CLI reports on
        # stderr exactly like every guard above it, but T201 is counted net-new.
        print(f"DATABASE_URL is not a valid database URL: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    engine = create_engine(sync_url)

    # Build OLD backend from the current env (factory cache).
    reset_backend_cache()
    old_backend = get_backend()

    # Build NEW backend by overlaying the *_NEW key onto the standard env
    # var, then asking the factory for a fresh instance.
    reset_backend_cache()
    with _env_overlay({key_env: new_key_value}):
        new_backend = get_backend()

    # Restore factory cache so future code paths see the OLD backend.
    reset_backend_cache()

    report = rotate_root(
        engine,
        old_backend=old_backend,
        new_backend=new_backend,
        new_kms_key_id=args.new_kms_key_id,
        batch_size=args.batch_size,
    )

    print(report.summary())
    if report.errors:
        print("\nFailures (rerun after addressing):")
        for org_id, exc_repr in report.errors:
            print(f"  org={org_id}: {exc_repr}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m server.crypto",
        description="KMS rotation + secrets utilities for PFactory.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    rotate = sub.add_parser(
        "rotate-root",
        help="Re-wrap every kms_data_keys row under a new KMS root.",
    )
    rotate.add_argument(
        "--new-kms-key-id",
        required=True,
        help="Human-readable identifier for the new root (audit log).",
    )
    rotate.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows per DB round trip (default: 100).",
    )
    rotate.set_defaults(func=_cmd_rotate_root)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
