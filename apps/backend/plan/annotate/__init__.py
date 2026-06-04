"""Document-honouring annotation (Phase D).

When a plan document is ingested PFactory *honours it*: it never rewrites the
source in place. Instead :func:`annotate_plan` turns the review's change-proposing
findings into :class:`SuggestedEdit` items anchored to spans of the original text
(each with a WHY + citation, #7), and assembles an *improved draft* that keeps the
original verbatim and appends a clearly-marked, cited "suggested edits" section —
so the engineer can accept, reject, or adopt the better version. Help, never
override.
"""

from plan.annotate.annotate import annotate_plan
from plan.annotate.models import AnnotationResult, SuggestedEdit

__all__ = ["AnnotationResult", "SuggestedEdit", "annotate_plan"]
