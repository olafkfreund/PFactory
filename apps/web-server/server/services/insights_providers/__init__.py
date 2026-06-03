"""
Multi-provider insights chat support.

Usage:
    from ..services.insights_providers import get_provider, detect_all_providers
"""

from .registry import get_provider, detect_all_providers

__all__ = ["get_provider", "detect_all_providers"]
