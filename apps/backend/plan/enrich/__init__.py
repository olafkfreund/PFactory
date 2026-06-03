"""Enrich stage — attach live organizational context to a plan.

Two adapter families:
  * **Infra adapters** (:mod:`plan.enrich.base`) — read-only introspection of
    running Kubernetes / OpenShift / Azure / AWS / GCP (issues #8–#10).
  * **Knowledge connectors** (:mod:`plan.enrich.knowledge`) — internal wikis,
    Backstage, repo docs (issue #11).

Everything here is **read-only by contract**: adapters must only call list/get/
describe APIs and never mutate the target environment.
"""

from plan.enrich.base import (
    InfraAdapter,
    InfraSnapshot,
    available_adapters,
    get_adapter,
    register_adapter,
)

__all__ = [
    "InfraAdapter",
    "InfraSnapshot",
    "available_adapters",
    "get_adapter",
    "register_adapter",
]
