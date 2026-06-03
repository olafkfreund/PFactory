"""Cloud discovery primitive (#133/#135) — read-only.

Two stages of the cloud flow ("do we get access" + "what do we find here"):

* :func:`access_check` — verify credentials work and report *who* we are
  (account + identity), without touching any resource.
* :func:`discover` — enumerate resources read-only into the normalized
  **inventory** dict that :func:`agents.diagrams.render_cloud_topology` renders
  and the cloud assessment framework consumes.

All provider CLI invocation goes through an injectable ``runner`` seam (default
``subprocess.run``) so tests run against canned JSON — no real cloud, no network.
Only ``describe``/``list``/``get`` style calls are issued; nothing mutates.

AWS, GCP and Azure are implemented. Discovery shells out to the host CLIs
(``aws`` / ``gcloud`` / ``az``); the in-container Prowler scan (see
``runner.py``) handles credential auth per provider.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable

__all__ = ["AccessResult", "CloudDiscoveryError", "access_check", "discover"]

_SUPPORTED = ("aws", "azure", "gcp")
_IMPLEMENTED = ("aws", "gcp", "azure")


class CloudDiscoveryError(Exception):
    """Raised for an unsupported provider or an unusable runner result."""


@dataclass
class AccessResult:
    """Outcome of the access/identity check."""

    ok: bool
    provider: str
    account: str | None = None
    identity: str | None = None
    error: str | None = None


@dataclass
class _Cmd:
    """A normalized command result (subset of CompletedProcess we rely on)."""

    returncode: int
    stdout: str


def _default_runner(argv: list[str]) -> _Cmd:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return _Cmd(returncode=proc.returncode, stdout=proc.stdout or "")


def _run(runner: Callable | None, argv: list[str]) -> _Cmd:
    r = (runner or _default_runner)(argv)
    # Allow tests to return a CompletedProcess-like object too.
    return _Cmd(
        returncode=getattr(r, "returncode", 1), stdout=getattr(r, "stdout", "") or ""
    )


def _profile_args(provider: str, profile: str | None) -> list[str]:
    if not profile:
        return []
    if provider == "aws":
        return ["--profile", profile]
    return []


def _aws_identity_name(arn: str) -> str | None:
    """Pull a human name out of an STS ARN.

    ``arn:aws:iam::123:user/Olaf.Freund`` → ``Olaf.Freund``;
    ``arn:aws:sts::123:assumed-role/Role/session`` → ``Role``.
    """
    if not arn:
        return None
    tail = arn.split(":")[-1]  # e.g. "user/Olaf.Freund" or "assumed-role/Role/sess"
    parts = tail.split("/")
    if parts[0] == "assumed-role" and len(parts) >= 2:
        return parts[1]
    return parts[-1] if len(parts) > 1 else None


# ── access check ─────────────────────────────────────────────────────────────


def _aws_access(profile: str | None, runner: Callable | None) -> AccessResult:
    cmd = _run(
        runner,
        ["aws", "sts", "get-caller-identity", "--output", "json"]
        + _profile_args("aws", profile),
    )
    if cmd.returncode != 0:
        return AccessResult(ok=False, provider="aws", error="sts get-caller-identity failed")
    try:
        data = json.loads(cmd.stdout)
    except (json.JSONDecodeError, ValueError):
        return AccessResult(ok=False, provider="aws", error="unparseable identity")
    return AccessResult(
        ok=True, provider="aws", account=data.get("Account"),
        identity=_aws_identity_name(data.get("Arn", "")),
    )


def _gcp_access(profile: str | None, runner: Callable | None) -> AccessResult:
    # ``profile`` overrides the project; otherwise read the active gcloud config.
    acct = _run(runner, ["gcloud", "auth", "list", "--filter=status:ACTIVE",
                         "--format=value(account)"])
    identity = acct.stdout.strip() or None
    if profile:
        project = profile
    else:
        proj = _run(runner, ["gcloud", "config", "get-value", "project", "--quiet"])
        project = proj.stdout.strip()
    if not project:
        return AccessResult(ok=False, provider="gcp", error="no GCP project configured")
    return AccessResult(ok=True, provider="gcp", account=project, identity=identity)


def _azure_access(profile: str | None, runner: Callable | None) -> AccessResult:
    argv = ["az", "account", "show", "--output", "json"]
    if profile:
        argv += ["--subscription", profile]
    cmd = _run(runner, argv)
    if cmd.returncode != 0:
        return AccessResult(ok=False, provider="azure", error="az account show failed")
    try:
        data = json.loads(cmd.stdout)
    except (json.JSONDecodeError, ValueError):
        return AccessResult(ok=False, provider="azure", error="unparseable identity")
    user = (data.get("user") or {}).get("name")
    return AccessResult(ok=True, provider="azure", account=data.get("id"), identity=user)


_ACCESS = {"aws": _aws_access, "gcp": _gcp_access, "azure": _azure_access}


def access_check(
    provider: str, *, profile: str | None = None, runner: Callable | None = None
) -> AccessResult:
    """Verify credentials and report account + identity (read-only)."""
    if provider not in _SUPPORTED:
        raise CloudDiscoveryError(
            f"unsupported provider {provider!r}; supported: {list(_SUPPORTED)}"
        )
    if provider not in _IMPLEMENTED:
        raise CloudDiscoveryError(
            f"provider {provider!r} discovery is not implemented yet "
            f"(implemented: {list(_IMPLEMENTED)})"
        )
    return _ACCESS[provider](profile, runner)


# ── discovery ────────────────────────────────────────────────────────────────


def _count(cmd: _Cmd, key: str) -> int | None:
    if cmd.returncode != 0:
        return None
    try:
        data = json.loads(cmd.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    val = data.get(key) if isinstance(data, dict) else data
    return len(val) if isinstance(val, list) else None


def _json_list_len(cmd: _Cmd, key: str | None = None) -> int | None:
    """Length of a JSON array in ``cmd.stdout`` (optionally under ``key``)."""
    if cmd.returncode != 0:
        return None
    try:
        data = json.loads(cmd.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    val = data.get(key) if (key and isinstance(data, dict)) else data
    return len(val) if isinstance(val, list) else None


def discover(
    provider: str,
    *,
    profile: str | None = None,
    regions: list[str] | None = None,
    services: list[str] | None = None,
    runner: Callable | None = None,
) -> dict:
    """Enumerate the account read-only into the normalized inventory dict.

    Returns a dict shaped for ``render_cloud_topology`` + assessment:
    ``{provider, account, identity, global: {...}, regions: {<r>: {...}}}``.
    ``global`` holds per-service summaries (AWS: ``s3``/``iam``; GCP:
    ``storage``/``iam``; Azure: ``storage``/``resource_groups``/``compute``).
    Findings are added by the assessment stage (#138), not here.
    """
    if provider not in _IMPLEMENTED:
        raise CloudDiscoveryError(
            f"provider {provider!r} discovery is not implemented yet "
            f"(implemented: {list(_IMPLEMENTED)})"
        )
    access = access_check(provider, profile=profile, runner=runner)
    inv: dict = {
        "provider": provider,
        "account": access.account,
        "identity": access.identity,
        "global": {},
        "regions": {},
    }
    if not access.ok:
        inv["error"] = access.error or "access check failed"
        return inv

    want = set(services or [])

    def wanted(svc: str) -> bool:
        return not want or svc in want

    if provider == "gcp":
        _gcp_discover(inv, access.account, wanted, runner)
    elif provider == "azure":
        _azure_discover(inv, access.account, wanted, runner)
    else:
        _aws_discover(inv, profile, regions, wanted, runner)
    return inv


def _gcp_discover(inv: dict, project: str | None, wanted, runner) -> None:
    """GCP global inventory via host ``gcloud`` (storage buckets + service accounts)."""
    proj = ["--project", project] if project else []
    if wanted("storage"):
        b = _run(runner, ["gcloud", "storage", "buckets", "list", "--format=json"] + proj)
        n = _json_list_len(b)
        if n is not None:
            inv["global"]["storage"] = {"count": n}
    if wanted("iam"):
        sa = _run(
            runner,
            ["gcloud", "iam", "service-accounts", "list", "--format=json"] + proj,
        )
        n = _json_list_len(sa)
        if n is not None:
            inv["global"]["iam"] = {"service_accounts": n}


def _azure_discover(inv: dict, subscription: str | None, wanted, runner) -> None:
    """Azure global inventory via host ``az`` (resource groups + storage + compute)."""
    sub = ["--subscription", subscription] if subscription else []
    if wanted("resource_groups"):
        g = _run(runner, ["az", "group", "list", "--output", "json"] + sub)
        n = _json_list_len(g)
        if n is not None:
            inv["global"]["resource_groups"] = {"count": n}
    if wanted("storage"):
        s = _run(runner, ["az", "storage", "account", "list", "--output", "json"] + sub)
        n = _json_list_len(s)
        if n is not None:
            inv["global"]["storage"] = {"count": n}
    if wanted("compute"):
        v = _run(runner, ["az", "vm", "list", "--output", "json"] + sub)
        n = _json_list_len(v)
        if n is not None:
            inv["global"]["compute"] = {"vms": n}


def _aws_discover(inv: dict, profile, regions, wanted, runner) -> None:
    """AWS global (s3/iam) + per-region (vpc/ec2/lambda) inventory."""
    prof = _profile_args("aws", profile)

    # ── global services ──────────────────────────────────────────────────────
    if wanted("s3"):
        # --query Buckets returns a bare JSON list of bucket objects.
        buckets = _run(
            runner,
            ["aws", "s3api", "list-buckets", "--query", "Buckets", "--output", "json"]
            + prof,
        )
        try:
            blist = json.loads(buckets.stdout) if buckets.returncode == 0 else []
            n = len(blist) if isinstance(blist, list) else None
        except (json.JSONDecodeError, ValueError):
            n = None
        if n is not None:
            inv["global"]["s3"] = {"count": n}
    if wanted("iam"):
        summ = _run(
            runner, ["aws", "iam", "get-account-summary", "--output", "json"] + prof
        )
        try:
            m = (
                json.loads(summ.stdout).get("SummaryMap", {})
                if summ.returncode == 0
                else {}
            )
        except (json.JSONDecodeError, ValueError):
            m = {}
        if m:
            inv["global"]["iam"] = {
                "users": m.get("Users"),
                "roles": m.get("Roles"),
                "policies": m.get("Policies"),
            }

    # ── per-region compute/network ───────────────────────────────────────────
    for region in regions or []:
        rprof = prof + ["--region", region]
        region_inv: dict = {}
        vpcs = _run(runner, ["aws", "ec2", "describe-vpcs", "--output", "json"] + rprof)
        v = _count(vpcs, "Vpcs")
        if v is not None:
            region_inv["vpcs"] = v
        inst = _run(
            runner, ["aws", "ec2", "describe-instances", "--output", "json"] + rprof
        )
        i = _count(inst, "Reservations")
        if i is not None:
            region_inv["instances"] = i
        lam = _run(
            runner, ["aws", "lambda", "list-functions", "--output", "json"] + rprof
        )
        fn = _count(lam, "Functions")
        if fn is not None:
            region_inv["lambdas"] = fn
        if region_inv:
            inv["regions"][region] = region_inv

    return inv
