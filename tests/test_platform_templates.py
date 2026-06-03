"""Tests for the shipped platform/infra template library (pytest, api lane).

The `library/` tier (frameworks/<fw>/library/) is discovered by the loader
alongside the curated built-in set, so these surface via the portal templates
API automatically. Each template must instantiate with realistic vars and
produce syntactically valid Python.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from templates_pkg.engine import load_templates_for_framework

REPO_ROOT = Path(__file__).resolve().parents[1]

# name -> realistic sample vars for instantiation.
_SAMPLES: dict[str, dict[str, str]] = {
    "servicenow-table-api.py.tmpl": {
        "table": "incident", "sysparm_query": "active=true",
        "expected_field": "state", "expected_value": "1",
    },
    "salesforce-rest-query.py.tmpl": {
        "soql": "SELECT Id FROM Account LIMIT 1",
        "expected_field": "Id", "min_records": "1",
    },
    "mulesoft-api.py.tmpl": {
        "path": "/api/v1/orders", "expected_status": "200", "expected_json_key": "data",
    },
    "kubernetes-service-health.py.tmpl": {
        "health_path": "/healthz", "ready_path": "/readyz",
    },
    "nginx-reverse-proxy.py.tmpl": {
        "path": "/", "expected_status": "200", "upstream_header": "x-upstream",
    },
    "load-balancer-health.py.tmpl": {
        "health_path": "/health", "sample_requests": "5",
    },
}


def test_library_templates_are_discovered() -> None:
    all_tmpls = load_templates_for_framework("pytest", root=REPO_ROOT)
    for name in _SAMPLES:
        assert name in all_tmpls, f"{name} not discovered by the loader"


def test_library_is_separate_from_the_curated_set() -> None:
    curated = load_templates_for_framework(
        "pytest", root=REPO_ROOT, include_library=False
    )
    assert len(curated) == 5  # the curated invariant is preserved
    for name in _SAMPLES:
        assert name not in curated  # library lives in library/, not templates/


@pytest.mark.parametrize("name", sorted(_SAMPLES))
def test_template_instantiates_to_valid_python(name: str) -> None:
    tmpl = load_templates_for_framework("pytest", root=REPO_ROOT)[name]
    result = tmpl.instantiate(**_SAMPLES[name])
    assert "${" not in result, f"{name} left an unsubstituted placeholder"
    compile(result, name, "exec")  # raises SyntaxError if not valid Python
    assert tmpl.metadata.description  # non-empty description
