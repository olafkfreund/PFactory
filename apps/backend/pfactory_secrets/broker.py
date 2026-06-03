"""
CredentialBroker — the agent-facing entry point for the secrets layer.

Responsibilities:
  - ``resolve_ref(ref)`` — resolve any secret reference through the backend
    factory, gated by egress for non-LOCAL backends.
  - ``resolve_cloud(provider)`` — obtain credentials for a cloud provider
    (``gcp`` / ``aws`` / ``azure`` / ``kubernetes``). First tries a
    backend-configured ref for that provider (the "fetch from a vault" head),
    then falls back to the existing ``core.mcp_credentials`` ambient chain.
  - Ephemeral **materialisation**: secret values that must be files (kubeconfig,
    GCP ADC JSON) are written to a per-task scratch dir at mode 0600 and wiped
    on ``close()`` / process exit. Resolved env vars are accumulated for the
    agent environment.

Egress posture (design decision D4): cloud resolution is **off by default**.
The broker resolves cloud creds only when ``egress_allowed=True`` (wired to the
project's ``.pfactory.yml`` ``egress.enabled`` in issue #8). Local backends
(``env`` / ``localfile``) never egress and are always allowed.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import stat
import tempfile
import weakref
from functools import lru_cache
from pathlib import Path

from pfactory_secrets import (
    EgressClass,
    SecretsError,
    SecretValue,
)

logger = logging.getLogger(__name__)

#: Operator-level mapping of cloud providers → backend refs. Separate from
#: ~/.pfactory/mcp-credentials.json (which maps ambient sources); issue #9
#: formalises the schema + a per-project .pfactory.yml block.
CREDENTIALS_CONFIG_PATH = Path.home() / ".pfactory" / "credentials.json"

_CLOUD_PROVIDERS = ("gcp", "aws", "azure", "kubernetes")


class CredentialBroker:
    """Resolve + materialise credentials for a single task.

    Use as a context manager (``with CredentialBroker(...) as b:``) or call
    ``close()`` to wipe materialised secret files.
    """

    def __init__(
        self,
        project_dir: Path | str | None = None,
        spec_dir: Path | str | None = None,
        *,
        egress_allowed: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self.spec_dir = Path(spec_dir).resolve() if spec_dir else None
        self.egress_allowed = egress_allowed
        self._scratch: Path | None = None
        self._materialised: list[Path] = []
        self._env: dict[str, str] = {}
        self._closed = False
        # WIF: per-provider minted creds cached with their TTL (#74).
        self._wif_cache: dict[str, object] = {}
        # Wipe materialised files even if close() is never called.
        self._finalizer = weakref.finalize(self, _wipe_paths, self._materialised)
        atexit.register(self.close)

    # -- public API ---------------------------------------------------------

    def resolve_ref(self, ref: str) -> SecretValue:
        """Resolve a single secret reference. Non-LOCAL backends require egress."""
        from pfactory_secrets.factory import get_secrets_backend
        from pfactory_secrets.refs import parse_ref

        parsed = parse_ref(ref)
        backend = get_secrets_backend(parsed.backend)
        if backend.egress_class() is not EgressClass.LOCAL and not self.egress_allowed:
            raise SecretsError(
                f"Refusing to resolve {ref!r}: backend {parsed.backend!r} egresses "
                f"({backend.egress_class().value}) and egress is not enabled for this "
                "task (set egress.enabled in .pfactory.yml)."
            )
        return backend.resolve(parsed)

    def resolve_cloud(self, provider: str):
        """Resolve credentials for a cloud provider → ``CredentialStatus``.

        Returns an unavailable status when egress is disabled. With egress on,
        tries a backend-configured ref first, then the ambient
        ``core.mcp_credentials`` chain.
        """
        from core.mcp_credentials import CredentialStatus, get_credential_status

        if provider not in _CLOUD_PROVIDERS:
            return CredentialStatus(False, f"unknown-provider:{provider}")
        if not self.egress_allowed:
            return CredentialStatus(False, "egress-disabled")

        # 0. WIF head: mint short-lived federated creds if configured (#74).
        wif_status = self._resolve_wif(provider)
        if wif_status is not None:
            return wif_status

        # 1. Backend-fetch head: an operator-configured ref for this provider.
        entry = _cloud_config().get(provider)
        if entry and entry.get("ref"):
            try:
                status = self._materialise_cloud_entry(provider, entry)
                if status is not None:
                    return status
            except (SecretsError, NotImplementedError, OSError) as exc:
                logger.warning(
                    "CredentialBroker: backend ref for %s failed (%s); "
                    "falling back to ambient credentials.",
                    provider,
                    exc,
                )

        # 2. Fall back to the existing ambient resolution chain.
        return get_credential_status(provider)

    def materialised_env(self) -> dict[str, str]:
        """The env vars resolved/materialised so far (copy)."""
        return dict(self._env)

    def apply_to_env(self, env: dict[str, str]) -> dict[str, str]:
        """Merge materialised env vars into ``env`` (broker values win)."""
        env.update(self._env)
        return env

    def close(self) -> None:
        """Wipe all materialised secret files."""
        if self._closed:
            return
        self._closed = True
        _wipe_paths(self._materialised)
        self._materialised.clear()
        self._env.clear()
        if self._scratch and self._scratch.exists():
            shutil.rmtree(self._scratch, ignore_errors=True)

    def __enter__(self) -> CredentialBroker:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _resolve_wif(self, provider: str):
        """Mint (or reuse cached) short-lived federated creds for ``provider``.

        Returns a ``CredentialStatus`` when WIF is configured for the provider,
        else ``None`` so ``resolve_cloud`` falls through to the other heads.
        Cached creds are reused until they near expiry, then re-minted — that's
        the refresh path. Fault-tolerant: a failed/unimplemented mint logs and
        returns ``None`` rather than breaking resolution.
        """
        import time

        from core.mcp_credentials import CredentialStatus

        entry = _wif_config().get(provider)
        if not entry:
            return None

        now = time.time()
        cached = self._wif_cache.get(provider)
        if cached is not None and not cached.expired(now):
            return CredentialStatus(True, f"wif:{provider}", dict(cached.env))

        try:
            from pfactory_secrets.wif import mint_wif

            creds = mint_wif(provider, entry, now=now)
        except Exception as exc:  # noqa: BLE001 - incl. NotImplementedError (gcp/azure)
            logger.warning(
                "CredentialBroker: WIF mint for %s failed (%s); "
                "falling back to other credential heads.",
                provider,
                exc,
            )
            return None

        if creds is None:
            return None
        self._wif_cache[provider] = creds
        self._env.update(creds.env)
        return CredentialStatus(True, f"wif:{provider}", dict(creds.env))

    def _materialise_cloud_entry(self, provider: str, entry: dict):
        """Resolve ``entry['ref']`` and materialise it per ``entry['kind']``."""
        from core.mcp_credentials import CredentialStatus

        secret = self.resolve_ref(entry["ref"])
        env_name = entry.get("as")
        kind = entry.get("kind", "env")
        if not env_name:
            return None

        if kind == "file":
            path = self.materialise_file(f"{provider}-cred", secret.value)
            self._env[env_name] = str(path)
            source = f"{secret.source}->file:{path.name}"
        else:
            self._env[env_name] = secret.value
            source = secret.source
        return CredentialStatus(
            available=True, source=f"broker:{source}", env_vars=dict(self._env)
        )

    def materialise_credential(self, name: str, entry) -> str:
        """Resolve a `.pfactory.yml` credential entry and expose it.

        ``entry`` carries ``ref`` / ``as_`` (env var) / ``kind`` (``env``|``file``).
        Returns the env-var name set. Raises ``SecretsError`` on failure.
        """
        ref = getattr(entry, "ref", None) or (
            entry.get("ref") if isinstance(entry, dict) else None
        )
        as_var = getattr(entry, "as_", None) or (
            entry.get("as") if isinstance(entry, dict) else None
        )
        kind = getattr(entry, "kind", None) or (
            entry.get("kind", "env") if isinstance(entry, dict) else "env"
        )
        if not ref or not as_var:
            raise SecretsError(f"Credential {name!r} needs both 'ref' and 'as'.")
        secret = self.resolve_ref(ref)
        if kind == "file":
            path = self.materialise_file(f"{name}-cred", secret.value)
            self._env[as_var] = str(path)
        else:
            self._env[as_var] = secret.value
        return as_var

    def materialise_file(self, name: str, content: str, mode: int = 0o600) -> Path:
        """Write ``content`` to a 0600 file in the per-task scratch dir; tracked
        for wipe on close."""
        scratch = self._ensure_scratch()
        path = scratch / name
        # Create exclusively, then write — never leave a world-readable window.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, mode)
        self._materialised.append(path)
        return path

    def _ensure_scratch(self) -> Path:
        if self._scratch is not None:
            return self._scratch
        if self.spec_dir is not None:
            base = self.spec_dir / ".pfactory-credentials"
            base.mkdir(parents=True, exist_ok=True)
        else:
            base = Path(tempfile.mkdtemp(prefix="pfactory-cred-"))
        os.chmod(base, stat.S_IRWXU)  # 0700
        self._scratch = base
        # Keep the finalizer tracking the same list object we append to.
        self._finalizer = weakref.finalize(self, _wipe_paths, self._materialised)
        return base


def inject_task_credentials(
    env: dict[str, str],
    project_dir: Path | str | None = None,
    spec_dir: Path | str | None = None,
) -> dict[str, str]:
    """Best-effort: merge broker-resolved cloud creds into ``env`` for a task.

    **Off by default** and fully fault-tolerant — it must never break agent
    creation. Credential resolution only happens when egress is explicitly
    enabled. The per-project ``.pfactory.yml`` ``egress.enabled`` gate is wired
    in issue #8; until then this honours the ``PFACTORY_EGRESS_ENABLED`` env
    flag, so the default path creates no broker and does no work.

    Materialised cred files are wiped at process exit; the agent subprocess
    inherits ``env`` (and reads any cred files it points to) during the run.
    """
    from pfactory_secrets.egress import egress_enabled

    if not egress_enabled(project_dir):
        return env
    try:
        broker = CredentialBroker(project_dir, spec_dir, egress_allowed=True)
        # 1. Operator-level named `credentials:` (~/.pfactory/credentials.json),
        #    then per-project `.pfactory.yml` `credentials:` — project wins on
        #    name collisions (applied last).
        named: dict = {**_operator_credentials(), **_project_credentials(project_dir)}
        for name, entry in named.items():
            try:
                broker.materialise_credential(name, entry)
            except SecretsError as exc:
                logger.warning("CredentialBroker: credential %r skipped: %s", name, exc)
        # 2. Cloud-provider creds from the operator config (backend-fetch head).
        for provider in _CLOUD_PROVIDERS:
            broker.resolve_cloud(provider)
        broker.apply_to_env(env)
    except Exception as exc:  # noqa: BLE001 - never break the agent on creds
        logger.warning("CredentialBroker: credential injection skipped: %s", exc)
    return env


def _project_credentials(project_dir: Path | str | None) -> dict:
    """Return the `.pfactory.yml` `credentials:` map (name -> entry), or {}."""
    if project_dir is None:
        return {}
    try:
        from pfactory_yml.parser import load_pfactory_yml

        cfg = load_pfactory_yml(Path(project_dir))
    except Exception:  # noqa: BLE001
        return {}
    return getattr(cfg, "credentials", None) or {} if cfg else {}


def _wipe_paths(paths: list[Path]) -> None:
    for p in list(paths):
        try:
            if Path(p).exists():
                Path(p).unlink()
        except OSError:  # pragma: no cover - best effort
            pass


@lru_cache(maxsize=1)
def _cloud_config() -> dict:
    """Read ~/.pfactory/credentials.json -> {provider: {ref, as, kind}}.

    Delegates to the typed loader (#71) and returns the dict shape callers
    already expect (``.get("ref")`` / ``.get("as")`` / ``.get("kind")``).
    """
    from pfactory_secrets.operator_config import load_operator_config

    cfg = load_operator_config(CREDENTIALS_CONFIG_PATH)
    return {name: e.model_dump(by_alias=True) for name, e in cfg.cloud.items()}


@lru_cache(maxsize=1)
def _operator_credentials() -> dict:
    """Read the operator config's named ``credentials:`` map (#71).

    Returns ``{name: {ref, as, kind}}`` — operator-defined named credential
    sets, the host-wide analogue of the per-project ``.pfactory.yml``
    ``credentials:`` block.
    """
    from pfactory_secrets.operator_config import load_operator_config

    cfg = load_operator_config(CREDENTIALS_CONFIG_PATH)
    return {name: e.model_dump(by_alias=True) for name, e in cfg.credentials.items()}


@lru_cache(maxsize=1)
def _wif_config() -> dict:
    """Read the operator config's ``wif:`` map (#74).

    Returns ``{provider: OperatorWifEntry}`` — workload-identity-federation
    config per cloud provider. Entries are returned as the validated model so
    ``wif.mint_wif`` can read attributes directly.
    """
    from pfactory_secrets.operator_config import load_operator_config

    cfg = load_operator_config(CREDENTIALS_CONFIG_PATH)
    return dict(cfg.wif)


def reset_config_cache() -> None:
    """Drop the cached credentials config (test/CLI helper)."""
    _cloud_config.cache_clear()
    _operator_credentials.cache_clear()
    _wif_config.cache_clear()


__all__ = [
    "CredentialBroker",
    "inject_task_credentials",
    "reset_config_cache",
    "CREDENTIALS_CONFIG_PATH",
]
