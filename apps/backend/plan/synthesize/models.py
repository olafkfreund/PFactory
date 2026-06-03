"""Synthesize-stage contract (issues #13/#14).

A synthesizer (Testing Strategy, CI/CD pipeline) produces a
:class:`SynthesizedArtifact`: a markdown spec document attached to the epic plus
a dedicated :class:`~plan.decompose.models.ChildIssue` so AIFactory implements it
as tracked work.
"""

from __future__ import annotations

from typing import Literal

from plan.decompose.models import ChildIssue
from pydantic import BaseModel

ArtifactKind = Literal["testing", "cicd"]


class SynthesizedArtifact(BaseModel):
    """A generated spec document + the child issue that tracks building it."""

    kind: ArtifactKind
    title: str
    document: str  # markdown spec attached to the epic
    child: ChildIssue
    filename: str = ""  # suggested path, e.g. docs/plans/<id>-testing-strategy.md
