"""Architecture lens (issue #15).

Asks: *is the decomposition well-shaped?* It rewards a clear, right-sized
breakdown and flags oversized epics (too many children), missing boundaries
(everything one undifferentiated kind), and vague epic/child titles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plan.review.lenses.base import register_lens
from plan.review.models import Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# Beyond this many children an epic is likely doing too much.
_MAX_CHILDREN = 12
# Titles shorter than this (in words) read as vague placeholders.
_MIN_TITLE_WORDS = 2
_VAGUE_TITLES = {"todo", "stuff", "misc", "thing", "things", "wip", "tbd"}


class ArchitectureLens:
    """Deterministic structural-quality heuristics over the decomposition."""

    name = "architecture"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:
        findings: list[Finding] = []
        score = 1.0

        n = len(epic.children)
        if n == 0:
            findings.append(
                Finding(
                    title="No decomposition to assess",
                    detail="The epic has no children, so architecture cannot be judged.",
                    severity="medium",
                    source=self.name,
                )
            )
            return LensScore(lens=self.name, score=0.4, findings=findings)

        if n > _MAX_CHILDREN:
            score -= 0.25
            findings.append(
                Finding(
                    title="Oversized epic",
                    detail=(
                        f"{n} children exceeds the recommended max of {_MAX_CHILDREN}; "
                        "split into multiple epics."
                    ),
                    severity="medium",
                    source=self.name,
                )
            )

        kinds = {c.kind for c in epic.children}
        if n >= 3 and len(kinds) == 1:
            score -= 0.15
            findings.append(
                Finding(
                    title="Missing boundaries between concerns",
                    detail=(
                        f"All {n} children share the kind '{next(iter(kinds))}'; the "
                        "work is not separated into distinct concerns."
                    ),
                    severity="low",
                    source=self.name,
                )
            )

        vague = [c.key for c in epic.children if _is_vague(c.title)]
        if _is_vague(epic.epic_title):
            vague.insert(0, "epic")
        if vague:
            score -= min(0.3, 0.1 * len(vague))
            findings.append(
                Finding(
                    title="Vague titles",
                    detail=f"Unclear titles on: {', '.join(vague)}.",
                    severity="low",
                    source=self.name,
                )
            )

        return LensScore(
            lens=self.name,
            score=round(max(0.0, min(1.0, score)), 4),
            findings=findings,
        )


def _is_vague(title: str) -> bool:
    words = title.strip().split()
    if len(words) < _MIN_TITLE_WORDS:
        return True
    return title.strip().lower() in _VAGUE_TITLES


register_lens(ArchitectureLens())
