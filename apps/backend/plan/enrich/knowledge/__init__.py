"""Knowledge connectors — internal wikis, Backstage, repo docs (issue #11)."""

from plan.enrich.knowledge.base import (
    KnowledgeConnector,
    KnowledgeRef,
    available_connectors,
    get_connector,
    register_connector,
)

__all__ = [
    "KnowledgeConnector",
    "KnowledgeRef",
    "available_connectors",
    "get_connector",
    "register_connector",
]
