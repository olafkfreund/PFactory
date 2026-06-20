"""Cloud assessment orchestrator (#133/#138) — auto-run a cloud target.

Ties the cloud stages together so a ``CloudProviderTarget`` runs end-to-end:

    discover (inventory)  →  Prowler in pfactory-runner-cloud (OCSF)  →
    assess + write report/diagram into findings/

:func:`run_cloud_assessment` is the seam the executor / portal calls. The Prowler
step shells out to Docker (read-only, creds mounted read-only); both the
discovery and Prowler steps are injectable so the orchestrator is unit-testable
without a real cloud or container.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .discovery import discover
from .report import assess_and_write

__all__ = ["build_prowler_command", "run_cloud_assessment"]

_IMAGE = "pfactory-runner-cloud:latest"
# Where each provider's ambient creds live on the host → mounted read-only.
# AWS keeps the container's default home (verified); GCP/Azure mount to a
# fixed path and run as the host uid so 0600 credential files are readable.
_CRED_MOUNTS = {
    "aws": (str(Path.home() / ".aws"), "/home/pfactory/.aws"),
    "gcp": (str(Path.home() / ".config" / "gcloud"), "/gcloud"),
    # Azure's ``az`` writes to AZURE_CONFIG_DIR (commandIndex.json); the host
    # login is mounted read-only at /azure-src and copied into writable scratch.
    "azure": (str(Path.home() / ".azure"), "/azure-src"),
}

# In-container shell that gives Azure's ``az`` a *writable* copy of the
# read-only host login, then execs Prowler. Keeps host creds read-only.
_AZURE_PREP = (
    'mkdir -p "$AZURE_CONFIG_DIR" && cp -r /azure-src/. "$AZURE_CONFIG_DIR"/ '
    '&& chmod -R u+rwX "$AZURE_CONFIG_DIR" && exec '
)


def build_prowler_command(
    provider: str,
    *,
    regions: list[str] | None = None,
    services: list[str] | None = None,
    output_dir: str = "/scratch",
    project_id: str | None = None,
) -> list[str]:
    """The in-container Prowler command (pure) — read-only OCSF scan.

    Per-provider auth flags: Azure uses ``--az-cli-auth`` (the mounted ``az``
    login); GCP optionally pins ``--project-id`` (else ADC's default project).
    """
    cmd = ["prowler", provider]
    if provider == "azure":
        cmd += ["--az-cli-auth"]
    if provider == "gcp" and project_id:
        cmd += ["--project-id", project_id]
    for s in services or []:
        cmd += ["--service", s]
    for r in regions or []:
        cmd += ["--region", r]
    cmd += ["--output-formats", "json-ocsf", "--output-directory", output_dir]
    return cmd


def _docker_argv(
    provider: str, profile: str | None, scratch_host: str, prowler_cmd: list[str]
) -> list[str]:
    """Full ``docker run`` argv wrapping the Prowler command (read-only creds)."""
    argv = ["docker", "run", "--rm", "--network=bridge"]
    mount = _CRED_MOUNTS.get(provider)
    if mount:
        argv += ["-v", f"{mount[0]}:{mount[1]}:ro"]
    argv += ["-v", f"{scratch_host}:/scratch:rw"]
    if provider == "aws":
        if profile:
            argv += ["-e", f"AWS_PROFILE={profile}"]
    elif provider == "gcp":
        # Read 0600 ADC as the host uid; point gcloud + ADC at the mount.
        argv += [
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/scratch",
            "-e",
            "CLOUDSDK_CONFIG=/gcloud",
            "-e",
            "GOOGLE_APPLICATION_CREDENTIALS=/gcloud/application_default_credentials.json",
        ]
    elif provider == "azure":
        argv += [
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/scratch",
            "-e",
            "AZURE_CONFIG_DIR=/scratch/azure-cfg",
        ]
    if provider == "azure":
        # Wrap in a shell so az gets a writable config copy before Prowler runs.
        argv += [_IMAGE, "sh", "-lc", _AZURE_PREP + shlex.join(prowler_cmd)]
    else:
        argv += [_IMAGE, *prowler_cmd]
    return argv


def _run_prowler(target, *, timeout: int = 1800) -> str:
    """Run Prowler in the cloud runner image and return the OCSF JSON (live)."""
    provider = getattr(target, "provider", "aws")
    profile = getattr(target, "profile", None)
    scan = getattr(target, "scan", None)
    services = list(getattr(scan, "services", []) or [])
    regions = list(getattr(target, "regions", []) or [])
    scratch = tempfile.mkdtemp(prefix="pfactory-prowler-")
    Path(scratch).chmod(0o777)  # the container's non-root user writes here
    # For GCP, ``profile`` carries the project id (else ADC's default project).
    project_id = profile if provider == "gcp" else None
    cmd = build_prowler_command(provider, regions=regions, services=services, project_id=project_id)
    argv = _docker_argv(provider, profile, scratch, cmd)
    # Prowler exits non-zero when it finds failures — that's expected, not an error.
    subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    ocsf = sorted(Path(scratch).glob("*.ocsf.json"))
    return ocsf[0].read_text(encoding="utf-8") if ocsf else "[]"


def run_cloud_assessment(
    spec_dir: Path,
    target,  # CloudProviderTarget (duck-typed)
    *,
    discover_fn: Callable | None = None,
    prowler_fn: Callable | None = None,
) -> dict:
    """Run a cloud target end-to-end and write the report into ``findings/``.

    Args:
        spec_dir: the task workspace (artifacts land under ``findings/``).
        target: a ``CloudProviderTarget`` (provider / profile / regions / scan).
        discover_fn / prowler_fn: injected in tests; default to the live
            :func:`discovery.discover` and the Docker Prowler run.

    Returns:
        ``assess_and_write``'s summary ``{verdict, fail_counts, paths}``.
    """
    provider = getattr(target, "provider", "aws")
    profile = getattr(target, "profile", None)
    regions = list(getattr(target, "regions", []) or [])
    scan = getattr(target, "scan", None)
    services = list(getattr(scan, "services", []) or [])
    fail_on = getattr(scan, "fail_on_severity", "high")

    inv = (discover_fn or discover)(provider, profile=profile, regions=regions, services=services)
    ocsf = (prowler_fn or _run_prowler)(target)
    return assess_and_write(spec_dir, inventory=inv, ocsf=ocsf, fail_on_severity=fail_on)
