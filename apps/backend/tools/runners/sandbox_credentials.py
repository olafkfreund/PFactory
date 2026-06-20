"""Sandbox credential injection for network-enabled lanes (#73).

Per-lane gating: the **unit** lane runs ``--network=none`` with NO credentials.
Only **network-enabled** lanes (api / integration, ``network != "none"``)
receive broker-resolved credentials, and only when **egress is explicitly
opted in** for the project. Resolved secret files (e.g. a kubeconfig) are
materialised 0600 on the host by the broker, bind-mounted **read-only** into
the container, and **wiped after the run** via :meth:`SandboxCredentials.wipe`.

This keeps the default path (unit lane, egress off) completely credential-free.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where a materialised kubeconfig is mounted inside the container.
_CONTAINER_KUBECONFIG = "/root/.kube/config"

#: Cloud providers the broker may resolve env credentials for.
_CLOUD_PROVIDERS = ("gcp", "aws", "azure", "kubernetes")


@dataclass
class SandboxCredentials:
    """Resolved sandbox credentials: env vars + read-only file mounts.

    ``files`` maps host path → container path (passed to ``DockerRunner.run``'s
    ``secret_files``). ``broker`` is retained so :meth:`wipe` can erase the
    materialised host files once the container has exited.
    """

    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    broker: object | None = None

    def wipe(self) -> None:
        """Erase materialised secret files (best-effort; never raises)."""
        broker = self.broker
        self.broker = None
        if broker is not None and hasattr(broker, "close"):
            try:
                broker.close()
            except Exception:  # noqa: BLE001 - cleanup must never raise
                logger.warning("sandbox credential wipe failed", exc_info=True)


def resolve_sandbox_credentials(
    project_dir: Path | str | None,
    spec_dir: Path | str | None,
    network: str | None,
) -> SandboxCredentials:
    """Resolve sandbox credentials, gated by lane (network) + egress opt-in.

    Returns **empty** credentials (the unit-lane case) when the lane is
    hermetic (``network`` in ``{None, "", "none"}``) or when egress is not
    enabled for the project. Otherwise resolves cloud creds via the
    CredentialBroker: cloud tokens as env vars, and a kubeconfig (when the
    broker materialises one) mounted read-only at ``/root/.kube/config``.

    Fully fault-tolerant: any resolution failure yields empty creds rather than
    breaking the lane.
    """
    # Hermetic lanes (unit) get nothing — no network, no creds.
    if network in (None, "", "none"):
        return SandboxCredentials()

    try:
        from pfactory_secrets.egress import egress_enabled

        if not egress_enabled(project_dir):
            return SandboxCredentials()
        from pfactory_secrets.broker import CredentialBroker

        broker = CredentialBroker(project_dir, spec_dir, egress_allowed=True)
    except Exception:  # noqa: BLE001 - never break the lane on creds
        logger.warning("sandbox credential resolution unavailable", exc_info=True)
        return SandboxCredentials()

    env: dict[str, str] = {}
    files: dict[str, str] = {}
    try:
        for provider in _CLOUD_PROVIDERS:
            status = broker.resolve_cloud(provider)
            if status and getattr(status, "available", False):
                env.update(getattr(status, "env_vars", {}) or {})
        # If the broker materialised a kubeconfig, mount that host file
        # read-only and repoint KUBECONFIG at the in-container path.
        kubeconfig = env.get("KUBECONFIG")
        if kubeconfig and Path(kubeconfig).exists():
            files[str(Path(kubeconfig).resolve())] = _CONTAINER_KUBECONFIG
            env["KUBECONFIG"] = _CONTAINER_KUBECONFIG
    except Exception:  # noqa: BLE001 - degrade to whatever resolved cleanly
        logger.warning("sandbox credential resolution failed", exc_info=True)

    return SandboxCredentials(env=env, files=files, broker=broker)


# ---------------------------------------------------------------------------
# Test-target credentials (#107) — "log in then test"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetCredentialSpec:
    """One ``.pfactory.yml`` ``test_credentials`` entry, resolved per run.

    ``ref`` is a secret reference the broker understands (``env:NAME`` /
    ``vault:path#field`` / cloud schemes). Its resolved value becomes the
    ``as_secret`` env var inside the sandbox. An optional ``username_ref``
    resolves the plaintext username into ``as_username``.

    Note on ``store:<id>`` refs: those are resolved **web-server-side** (it
    owns the encrypted DB; the backend agent runs in a separate venv without
    the DB driver) and arrive here already materialised as an ``env:`` ref,
    so this resolver only ever sees broker-resolvable schemes.
    """

    name: str
    ref: str
    as_secret: str
    as_username: str | None = None
    username_ref: str | None = None


def resolve_test_target_credentials(
    specs: Iterable[TargetCredentialSpec] | None,
    project_dir: Path | str | None,
    spec_dir: Path | str | None,
    network: str | None,
) -> SandboxCredentials:
    """Resolve test-target credentials into sandbox env, gated like #73.

    Returns **empty** credentials when the lane is hermetic
    (``network`` in ``{None, "", "none"}``), when there are no specs, or when
    egress is not enabled for the project — so the default hermetic path stays
    completely credential-free. Otherwise each spec's ``ref`` is resolved via
    the CredentialBroker and mapped to its ``as_secret`` (and optional
    ``as_username``) env var.

    Fully fault-tolerant: a per-spec failure is logged and skipped rather than
    breaking the lane. The returned ``broker`` is retained so the caller can
    ``wipe()`` materialised files after the run.
    """
    specs = list(specs or [])
    if network in (None, "", "none") or not specs:
        return SandboxCredentials()

    try:
        from pfactory_secrets.egress import egress_enabled

        if not egress_enabled(project_dir):
            return SandboxCredentials()
        from pfactory_secrets.broker import CredentialBroker

        broker = CredentialBroker(project_dir, spec_dir, egress_allowed=True)
    except Exception:  # noqa: BLE001 - never break the lane on creds
        logger.warning("test-target credential resolution unavailable", exc_info=True)
        return SandboxCredentials()

    env: dict[str, str] = {}
    for spec in specs:
        if spec.ref.startswith("store:"):
            # store: refs are materialised web-server-side; if one reaches the
            # backend it wasn't expanded upstream — skip rather than guess.
            logger.warning(
                "test credential %r has an unresolved store: ref; expected the "
                "web-server to materialise it. Skipping.",
                spec.name,
            )
            continue
        try:
            env[spec.as_secret] = broker.resolve_ref(spec.ref).value
            if spec.as_username and spec.username_ref:
                env[spec.as_username] = broker.resolve_ref(spec.username_ref).value
        except Exception:  # noqa: BLE001 - one bad ref must not break the lane
            logger.warning("failed to resolve test credential %r", spec.name, exc_info=True)

    return SandboxCredentials(env=env, broker=broker)


def config_to_credential_specs(
    config: object | None, target_name: str | None
) -> list[TargetCredentialSpec]:
    """Build resolver specs for a target's ``ref``-auth from a parsed config.

    Bridges the ``.pfactory.yml`` schema (task 3) to the resolver (task 2): if
    ``target_name``'s ``auth`` is a ``ref`` auth, look up the named
    ``test_credentials`` entry and turn it into a :class:`TargetCredentialSpec`.

    Returns ``[]`` when there is no config, no such target, the target has no
    ``ref``-auth, or the named credential is absent. Reads ``config``
    duck-typed (a ``PFactoryConfig``) so this module needs no schema import.
    """
    if config is None or not target_name:
        return []
    test_credentials = getattr(config, "test_credentials", None) or {}
    lookup = getattr(config, "lookup_target", None)
    target = lookup(target_name) if callable(lookup) else None
    if target is None:
        return []
    auth = getattr(target, "auth", None)
    if auth is None or getattr(auth, "type", None) != "ref":
        return []
    entry = test_credentials.get(getattr(auth, "ref", None))
    if entry is None:
        return []
    return [
        TargetCredentialSpec(
            name=auth.ref,
            ref=entry.ref,
            as_secret=entry.as_secret,
            as_username=entry.as_username,
            username_ref=entry.username_ref,
        )
    ]


__all__ = [
    "SandboxCredentials",
    "TargetCredentialSpec",
    "config_to_credential_specs",
    "resolve_sandbox_credentials",
    "resolve_test_target_credentials",
]
