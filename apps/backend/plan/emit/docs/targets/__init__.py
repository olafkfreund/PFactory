"""Documentation targets — pluggable sinks for a rendered DocBundle."""

from .backstage import BackstageTarget
from .base import DocsTarget
from .confluence import ConfluenceTarget
from .github_writer import GitHubContentsWriter
from .repo import RepoDocsTarget

__all__ = [
    "BackstageTarget",
    "ConfluenceTarget",
    "DocsTarget",
    "GitHubContentsWriter",
    "RepoDocsTarget",
]
