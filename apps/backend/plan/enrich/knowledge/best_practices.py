"""Best-practice references matched to a plan's topics (knowledge connector).

PFactory's "search best practices" pillar: given the plan text, surface the
authoritative best-practice guidance relevant to what's being built (EKS,
networking/security groups, disaster recovery, data services, CI/CD, …). These
are real, well-known references; the connector selects the subset that matches
the plan's detected topics so the planner and review gates can cite them.

It is read-only and offline-safe (a curated catalogue), so the demo doesn't
depend on a live web call; swap in a live search/MCP source behind the same
``search()`` contract when one is configured.
"""

from __future__ import annotations

from plan.enrich.knowledge.base import (
    KnowledgeConnector,
    KnowledgeRef,
    register_connector,
)

# topic keyword(s) -> best-practice references
_CATALOGUE: list[tuple[tuple[str, ...], KnowledgeRef]] = [
    (("eks", "kubernetes", "k8s"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="Amazon EKS Best Practices Guide",
        uri="https://docs.aws.amazon.com/eks/latest/best-practices/",
        snippet="Security, reliability, scalability and cost guidance for EKS clusters.",
        score=0.95)),
    (("security group", "0.0.0.0/0", "ingress", "network", "exposure"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="AWS Well-Architected — Security Pillar (network protection)",
        uri="https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/",
        snippet="Restrict ingress to known CIDRs; front public traffic with an ALB/WAF; default-deny.",
        score=0.93)),
    (("disaster", "dr", "failover", "rpo", "rto", "backup", "multi-region", "multi region"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="AWS Well-Architected — Reliability Pillar (DR strategies)",
        uri="https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/disaster-recovery-dr-objectives.html",
        snippet="Define RPO/RTO; choose backup/pilot-light/warm-standby/active-active; test failover.",
        score=0.94)),
    (("rds", "postgres", "database", "replica"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="Amazon RDS — High Availability & Cross-Region Replicas",
        uri="https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html",
        snippet="Multi-AZ for HA; cross-region read replicas for DR; automated backups + snapshots.",
        score=0.9)),
    (("redis", "elasticache", "kafka", "msk", "queue"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="ElastiCache / MSK resiliency best practices",
        uri="https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/BestPractices.html",
        snippet="Replication groups, multi-AZ failover, and backup for cache and streaming tiers.",
        score=0.85)),
    (("secret", "credentials", "iam", "irsa"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="EKS — IRSA & secrets management",
        uri="https://docs.aws.amazon.com/eks/latest/userguide/iam-roles-for-service-accounts.html",
        snippet="Least-privilege IAM via IRSA; inject secrets from Secrets Manager via the CSI driver.",
        score=0.88)),
    (("ci/cd", "cicd", "pipeline", "deploy", "helm"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="CI/CD for Kubernetes — image scanning & progressive delivery",
        uri="https://docs.aws.amazon.com/eks/latest/best-practices/cicd.html",
        snippet="Scan images in CI; deploy via Helm; use canary/blue-green for safe rollout.",
        score=0.82)),
    (("microservice", "api", "service mesh", "networkpolicy"), KnowledgeRef(
        connector="best-practices", kind="best-practice",
        title="Microservices on EKS — isolation & NetworkPolicies",
        uri="https://docs.aws.amazon.com/eks/latest/best-practices/network-management.html",
        snippet="Default-deny NetworkPolicies, mesh mTLS, and per-service resource governance.",
        score=0.8)),
]


@register_connector
class BestPracticesConnector(KnowledgeConnector):
    """Curated best-practice references selected by plan topic."""

    name = "best-practices"

    def available(self) -> bool:
        return True

    def search(self, query: str, *, limit: int = 10) -> list[KnowledgeRef]:
        low = query.lower()
        hits: list[KnowledgeRef] = []
        for keywords, ref in _CATALOGUE:
            if any(k in low for k in keywords):
                hits.append(ref)
        hits.sort(key=lambda r: r.score, reverse=True)
        return hits[:limit]
