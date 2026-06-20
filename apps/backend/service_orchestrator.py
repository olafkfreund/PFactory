"""Backward compatibility shim - import from services.orchestrator instead."""

from services.orchestrator import (
    OrchestrationResult,
    ServiceConfig,
    ServiceContext,
    ServiceOrchestrator,
    get_service_config,
    is_multi_service_project,
)

__all__ = [
    "OrchestrationResult",
    "ServiceConfig",
    "ServiceContext",
    "ServiceOrchestrator",
    "get_service_config",
    "is_multi_service_project",
]
