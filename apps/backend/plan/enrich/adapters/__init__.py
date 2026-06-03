"""Concrete read-only infra adapters (Kubernetes, OpenShift, cloud).

Each adapter lives in its own module and self-registers via ``@register_adapter``
on import. Import the specific module you need (e.g.
``from plan.enrich.adapters import kubernetes``) to register it.
"""
