"""Documentation targets — pluggable sinks for a rendered DocBundle."""

from .base import DocsTarget
from .repo import RepoDocsTarget

__all__ = ["DocsTarget", "RepoDocsTarget"]
