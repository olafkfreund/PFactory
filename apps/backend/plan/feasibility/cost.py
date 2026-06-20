"""Cost estimation — price the plan's resource shape (Phase C).

Two steps: (1) extract a coarse resource shape from the plan text + live
enrichment, (2) price each line. Pricing tries a real provider client (AWS Price
List · Azure Retail Prices · GCP Billing Catalog) and falls back to a static
price book, so an estimate is ALWAYS produced — with a ``confidence`` label and a
``source`` citation reflecting how much was priced for real (help, never
override; a pricing outage degrades confidence, it doesn't fail the run).
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import TYPE_CHECKING, Protocol

from plan.decompose.models import CostEstimate, CostLine

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan


class ResourceLine:
    """An internal proposed-resource line to be priced."""

    def __init__(self, provider: str, service: str, sku: str, quantity: float, region: str = ""):
        self.provider = provider
        self.service = service
        self.sku = sku
        self.quantity = quantity
        self.region = region

    def key(self) -> str:
        return f"{self.provider}:{self.service}:{self.sku}"


# ── resource extraction ──────────────────────────────────────────────────

# Common managed services → (provider, service, default sku) when mentioned.
_SERVICE_HINTS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"(?i)\beks\b|kubernetes|k8s"), "aws", "eks", "cluster"),
    (re.compile(r"(?i)\brds\b|postgres|mysql|aurora"), "aws", "rds", "db.r6g.large"),
    (
        re.compile(r"(?i)\belasticache\b|\bredis\b|memcached"),
        "aws",
        "elasticache",
        "cache.r6g.large",
    ),
    (re.compile(r"(?i)\bs3\b|object storage|bucket"), "aws", "s3", "standard-1tb"),
    (re.compile(r"(?i)\balb\b|load balancer|nlb"), "aws", "elb", "alb"),
    (re.compile(r"(?i)\baks\b"), "azure", "aks", "cluster"),
    (re.compile(r"(?i)\bgke\b"), "gcp", "gke", "cluster"),
]
# AWS instance type like m5.large, c6g.xlarge, db.r6g.large.
_INSTANCE_RE = re.compile(
    r"\b((?:db\.)?[a-z][0-9][a-z]?\.[0-9]*x?(?:large|medium|small|micro|nano|metal))\b"
)


def _plan_text(plan: NormalizedPlan) -> str:
    parts = [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
    return "\n".join(p for p in parts if p)


def _region_multiplier(text: str, infra_regions: list[str]) -> int:
    if re.search(r"(?i)multi[\s-]?region", text):
        return max(2, len(infra_regions) or 2)
    return max(1, len(infra_regions) or 1)


def extract_resources(plan: NormalizedPlan, epic: EpicPlan | None = None) -> list[ResourceLine]:
    """Best-effort coarse resource shape from plan text + live enrichment."""
    text = _plan_text(plan)
    low = text.lower()
    lines: list[ResourceLine] = []

    # Regions surfaced by enrichment (and the proposed multi-region multiplier).
    infra_regions: list[str] = []
    instance_types: dict[str, int] = {}
    for entry in getattr(plan.enrichment, "infra", []) or []:
        if not isinstance(entry, dict):
            continue
        res = entry.get("resources") or {}
        infra_regions.extend(res.get("regions") or [])
        for itype, count in (res.get("instance_types") or {}).items():
            instance_types[itype] = instance_types.get(itype, 0) + int(count or 0)
    region = (infra_regions[0] if infra_regions else "") or "us-east-1"
    mult = _region_multiplier(text, sorted(set(infra_regions)))

    # Managed services mentioned in the plan.
    for pat, provider, service, sku in _SERVICE_HINTS:
        if pat.search(text):
            qty = mult if service in {"eks", "aks", "gke", "rds", "elasticache"} else 1
            lines.append(ResourceLine(provider, service, sku, qty, region))

    # Explicit instance types in the text (proposed compute).
    for itype in sorted(set(_INSTANCE_RE.findall(low))):
        service = "rds" if itype.startswith("db.") else "ec2"
        lines.append(ResourceLine("aws", service, itype, mult, region))

    # Existing footprint from live enrichment (baseline reference).
    for itype, count in instance_types.items():
        if count:
            lines.append(ResourceLine("aws", "ec2", itype, count, region))

    # De-dup by key, summing quantities.
    merged: dict[str, ResourceLine] = {}
    for ln in lines:
        if ln.key() in merged:
            merged[ln.key()].quantity += ln.quantity
        else:
            merged[ln.key()] = ln
    return list(merged.values())


# ── pricing clients ──────────────────────────────────────────────────────


class PricingClient(Protocol):
    provider: str

    def monthly_usd(self, line: ResourceLine) -> float | None:
        """Return a real monthly USD price for a line, or None if unknown."""
        ...


_HOURS_PER_MONTH = 730.0

# Rough monthly USD per (service, sku-or-default) — the always-available fallback.
_STATIC_BOOK: dict[str, float] = {
    "aws:eks:cluster": 73.0,  # control plane
    "aws:rds:db.r6g.large": 290.0,  # Multi-AZ ballpark
    "aws:elasticache:cache.r6g.large": 180.0,
    "aws:s3:standard-1tb": 23.0,
    "aws:elb:alb": 22.0,
    "azure:aks:cluster": 73.0,
    "gcp:gke:cluster": 73.0,
}
# Per-vCPU/size fallback for arbitrary EC2/RDS instance types.
_SIZE_FACTOR: dict[str, float] = {
    "nano": 4,
    "micro": 8,
    "small": 16,
    "medium": 35,
    "large": 70,
    "xlarge": 140,
    "2xlarge": 280,
    "4xlarge": 560,
    "metal": 1500,
}


def _static_price(line: ResourceLine) -> float:
    if line.key() in _STATIC_BOOK:
        return _STATIC_BOOK[line.key()]
    for size, monthly in _SIZE_FACTOR.items():
        if line.sku.endswith(size):
            base = monthly * (2 if line.sku.startswith("db.") else 1)  # Multi-AZ-ish
            return base
    return 50.0  # generic unknown resource


_AWS_REGION_LOCATION: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-central-1": "EU (Frankfurt)",
}


class AwsPricingClient:
    """Real AWS Price List pricing for EC2 instances (best-effort, guarded)."""

    provider = "aws"

    def __init__(self, session: object | None = None) -> None:
        self._session = session

    def monthly_usd(self, line: ResourceLine) -> float | None:
        if line.provider != "aws" or line.service != "ec2":
            return None
        location = _AWS_REGION_LOCATION.get(line.region)
        if location is None:
            return None
        try:  # pragma: no cover - exercised only with live AWS creds
            import boto3

            client = (self._session or boto3.Session()).client("pricing", region_name="us-east-1")
            resp = client.get_products(
                ServiceCode="AmazonEC2",
                Filters=[
                    {"Type": "TERM_MATCH", "Field": "instanceType", "Value": line.sku},
                    {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                    {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                    {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                    {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                    {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                ],
                MaxResults=1,
            )
            for item in resp.get("PriceList", []):
                hourly = _aws_on_demand_hourly(item)
                if hourly is not None:
                    return round(hourly * _HOURS_PER_MONTH, 2)
        except Exception:
            return None
        return None


def _aws_on_demand_hourly(price_item: str) -> float | None:
    try:
        doc = json.loads(price_item)
        on_demand = doc["terms"]["OnDemand"]
        for term in on_demand.values():
            for dim in term["priceDimensions"].values():
                usd = dim["pricePerUnit"].get("USD")
                if usd:
                    return float(usd)
    except Exception:
        return None
    return None


class AzurePricingClient:
    """Azure Retail Prices API — public, no auth (best-effort)."""

    provider = "azure"

    def monthly_usd(self, line: ResourceLine) -> float | None:
        if line.provider != "azure":
            return None
        try:  # pragma: no cover - network call
            url = (
                "https://prices.azure.com/api/retail/prices?$filter="
                "serviceName eq 'Azure Kubernetes Service'"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            items = data.get("Items") or []
            if items and items[0].get("retailPrice"):
                return round(float(items[0]["retailPrice"]) * _HOURS_PER_MONTH, 2)
        except Exception:
            return None
        return None


class GcpPricingClient:
    """GCP Cloud Billing Catalog — needs an API key; degrades to None."""

    provider = "gcp"

    def monthly_usd(self, line: ResourceLine) -> float | None:
        return None  # requires billing API key; static fallback covers GCP


def _default_clients() -> list[PricingClient]:
    return [AwsPricingClient(), AzurePricingClient(), GcpPricingClient()]


def estimate_cost(
    plan: NormalizedPlan,
    epic: EpicPlan | None = None,
    *,
    clients: list[PricingClient] | None = None,
) -> CostEstimate:
    """Estimate monthly spend for the plan's resource shape; never raises."""
    shape = extract_resources(plan, epic)
    if not shape:
        return CostEstimate(monthly_usd=0.0, confidence="low", source="no-resources")

    by_provider = {c.provider: c for c in (clients if clients is not None else _default_clients())}
    cost_lines: list[CostLine] = []
    sources: set[str] = set()
    real_priced = 0
    for ln in shape:
        unit = None
        client = by_provider.get(ln.provider)
        if client is not None:
            try:
                unit = client.monthly_usd(ln)
            except Exception:
                unit = None
        if unit is not None:
            real_priced += 1
            sources.add(f"{ln.provider}-price-list")
        else:
            unit = _static_price(ln)
            sources.add("static-fallback")
        cost_lines.append(
            CostLine(
                resource=ln.key(),
                quantity=ln.quantity,
                monthly_usd=round(unit * ln.quantity, 2),
                detail=f"{ln.quantity:g}× {ln.sku} in {ln.region or 'default'}",
            )
        )

    total = round(sum(c.monthly_usd for c in cost_lines), 2)
    if real_priced == len(shape):
        confidence = "high"
    elif real_priced > 0:
        confidence = "medium"
    else:
        confidence = "low"
    return CostEstimate(
        monthly_usd=total,
        currency="USD",
        lines=cost_lines,
        confidence=confidence,
        source=", ".join(sorted(sources)),
    )
