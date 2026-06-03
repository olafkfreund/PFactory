"""PFactory test-file template engine.

Re-exports the public API from :mod:`templates_pkg.engine` so callers can use::

    from templates_pkg import load_template, load_templates_for_framework, TemplateFile
"""

from templates_pkg.engine import (
    TemplateError,
    TemplateFile,
    TemplateMetadata,
    load_template,
    load_templates_for_framework,
)

__all__ = [
    "TemplateError",
    "TemplateFile",
    "TemplateMetadata",
    "load_template",
    "load_templates_for_framework",
]
