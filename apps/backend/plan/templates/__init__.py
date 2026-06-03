"""PFactory templates package (issue #26).

Re-exports the template/policy contracts from :mod:`plan.templates.models`
plus the loader helpers that discover, select, and enforce templates against
a :class:`~plan.models.NormalizedPlan`.
"""

from __future__ import annotations

from plan.templates.loader import (
    build_context,
    enforce,
    load_template,
    load_templates,
    select_template,
)
from plan.templates.models import (
    Policy,
    PolicyRule,
    Template,
    TemplateMetadata,
)

__all__ = [
    "Policy",
    "PolicyRule",
    "Template",
    "TemplateMetadata",
    "build_context",
    "enforce",
    "load_template",
    "load_templates",
    "select_template",
]
