"""Read-only AWS inventory + posture adapter (#10).

Discovers EC2 instances, EKS clusters, and a security-group posture summary
for an AWS account/region. Strictly read-only: only ``describe``/``list`` calls
are ever issued.

The adapter never imports ``boto3`` at module import time. Discovery runs
against an injected *reader* object exposing simple read methods::

    reader.list_ec2_instances()    -> list[dict]
    reader.list_eks_clusters()     -> list[dict]
    reader.list_security_groups()  -> list[dict]

In production the reader is built lazily from ``boto3`` (see
:func:`_build_reader`). Tests inject a fake reader and need no boto3 installed.
"""

from __future__ import annotations

import os
from typing import Protocol

from ..base import InfraAdapter, InfraSnapshot, register_adapter

# Kubernetes versions at/below this are flagged as outdated in findings.
_OUTDATED_K8S_BELOW = (1, 28)
# Security-group rules open to the world.
_PUBLIC_CIDRS = {"0.0.0.0/0", "::/0"}


class AwsReader(Protocol):
    """Read-only seam consumed by :class:`AwsAdapter`."""

    def list_ec2_instances(self) -> list[dict]: ...
    def list_eks_clusters(self) -> list[dict]: ...
    def list_security_groups(self) -> list[dict]: ...


def _parse_k8s_version(version: str | None) -> tuple[int, int] | None:
    """Parse ``"1.27"`` / ``"1.27.3"`` -> ``(1, 27)``; ``None`` if unparsable."""
    if not version:
        return None
    parts = str(version).lstrip("v").split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return None


def _sg_is_public(sg: dict) -> bool:
    """True if a security group has any ingress rule open to the world."""
    if sg.get("public") is True:
        return True
    for rule in sg.get("ip_permissions", []) or []:
        for rng in rule.get("ip_ranges", []) or []:
            if rng.get("cidr_ip") in _PUBLIC_CIDRS:
                return True
        for rng in rule.get("ipv6_ranges", []) or []:
            if rng.get("cidr_ipv6") in _PUBLIC_CIDRS:
                return True
    return False


def _build_reader(region: str | None) -> AwsReader:  # pragma: no cover
    """Lazily construct a real AWS reader from boto3 (read-only calls only)."""
    import boto3

    session = boto3.Session(region_name=region or None)

    class _SdkReader:
        def list_ec2_instances(self) -> list[dict]:
            ec2 = session.client("ec2")
            out: list[dict] = []
            for res in ec2.describe_instances().get("Reservations", []):
                for inst in res.get("Instances", []):
                    out.append({
                        "instance_id": inst.get("InstanceId"),
                        "instance_type": inst.get("InstanceType"),
                        "state": inst.get("State", {}).get("Name"),
                        "region": region,
                    })
            return out

        def list_eks_clusters(self) -> list[dict]:
            eks = session.client("eks")
            names = eks.list_clusters().get("clusters", [])
            out: list[dict] = []
            for name in names:
                desc = eks.describe_cluster(name=name).get("cluster", {})
                out.append({
                    "name": desc.get("name", name),
                    "version": desc.get("version"),
                    "status": desc.get("status"),
                    "region": region,
                })
            return out

        def list_security_groups(self) -> list[dict]:
            ec2 = session.client("ec2")
            out: list[dict] = []
            for sg in ec2.describe_security_groups().get("SecurityGroups", []):
                out.append({
                    "group_id": sg.get("GroupId"),
                    "group_name": sg.get("GroupName"),
                    "ip_permissions": [
                        {
                            "ip_ranges": p.get("IpRanges", []),
                            "ipv6_ranges": p.get("Ipv6Ranges", []),
                        }
                        for p in sg.get("IpPermissions", [])
                    ],
                })
            return out

    return _SdkReader()


@register_adapter
class AwsAdapter(InfraAdapter):
    """Read-only AWS account inventory + posture adapter."""

    name = "aws"

    def __init__(
        self,
        *,
        target: str = "",
        reader: AwsReader | None = None,
        **options: object,
    ) -> None:
        super().__init__(target=target, **options)
        self._reader = reader

    # ── availability ────────────────────────────────────────────────────
    def available(self) -> bool:
        """True if a reader is injected or AWS credentials exist in the env."""
        if self._reader is not None:
            return True
        has_keys = bool(
            os.environ.get("AWS_ACCESS_KEY_ID")
            and os.environ.get("AWS_SECRET_ACCESS_KEY")
        )
        has_profile = bool(os.environ.get("AWS_PROFILE"))
        return has_keys or has_profile

    def _region(self) -> str | None:
        return (
            str(self.options.get("region"))
            if self.options.get("region")
            else os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )

    def _reader_or_build(self) -> AwsReader:
        if self._reader is not None:
            return self._reader
        return _build_reader(self._region())

    # ── discovery ───────────────────────────────────────────────────────
    def discover(self) -> InfraSnapshot:
        """Return a read-only snapshot of the AWS account."""
        reader = self._reader_or_build()

        instances = list(reader.list_ec2_instances())
        clusters = list(reader.list_eks_clusters())
        security_groups = list(reader.list_security_groups())

        workloads: list[dict] = []
        findings: list[str] = []
        regions: set[str] = set()
        instance_types: dict[str, int] = {}

        for inst in instances:
            region = inst.get("region")
            if region:
                regions.add(region)
            itype = inst.get("instance_type")
            if itype:
                instance_types[itype] = instance_types.get(itype, 0) + 1

        for c in clusters:
            name = c.get("name", "unknown")
            version = c.get("version")
            region = c.get("region")
            if region:
                regions.add(region)
            workloads.append({
                "kind": "eks_cluster",
                "name": name,
                "version": version,
                "status": c.get("status"),
                "region": region,
            })
            parsed = _parse_k8s_version(version)
            if parsed is not None and parsed < _OUTDATED_K8S_BELOW:
                findings.append(
                    f"EKS cluster {name} runs an outdated k8s version {version}"
                )

        public_sgs = [sg for sg in security_groups if _sg_is_public(sg)]

        if instances:
            findings.append(f"{len(instances)} EC2 instances")
        if public_sgs:
            names = ", ".join(
                str(sg.get("group_id") or sg.get("group_name")) for sg in public_sgs
            )
            findings.append(
                f"{len(public_sgs)} security groups open to the world ({names})"
            )

        resources = {
            "ec2_instance_count": len(instances),
            "eks_cluster_count": len(clusters),
            "security_group_count": len(security_groups),
            "instance_types": instance_types,
            "regions": sorted(regions),
        }
        policies = [
            {
                "group_id": sg.get("group_id"),
                "group_name": sg.get("group_name"),
                "public": True,
            }
            for sg in public_sgs
        ]

        return InfraSnapshot(
            adapter=self.name,
            target=self.target,
            available=True,
            workloads=workloads,
            resources=resources,
            policies=policies,
            findings=findings,
            raw={
                "ec2_instances": instances,
                "eks_clusters": clusters,
                "security_groups": security_groups,
            },
        )


__all__: list[str] = ["AwsAdapter", "AwsReader"]
