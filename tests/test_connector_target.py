"""Tests for the SaaS connector target type (#111).

A first-class `connector` target (ServiceNow / Salesforce / SAP / MuleSoft) that
reuses the http + credential-vault plumbing and maps each platform to its
`library/` check template + Gen-Functional guidance.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from pfactory_yml.schema import (
    CONNECTOR_PLATFORMS,
    ConnectorTarget,
    PFactoryConfig,
    connector_platform_info,
)

_LIBRARY = Path(__file__).resolve().parents[1] / "frameworks" / "pytest" / "library"


def _cfg(target: dict, **extra) -> dict:
    base = {
        "version": 1,
        "targets": [target],
        "default_target": target["name"],
        "egress": {"enabled": True},
    }
    base.update(extra)
    return base


def test_connector_target_validates() -> None:
    t = ConnectorTarget.model_validate(
        {
            "name": "snow",
            "type": "connector",
            "platform": "servicenow",
            "base_url": "https://acme.service-now.com",
            "entities": ["incident", "change_request"],
        }
    )
    assert t.platform == "servicenow"
    assert t.entities == ["incident", "change_request"]


def test_unknown_platform_rejected() -> None:
    with pytest.raises(ValidationError):
        ConnectorTarget.model_validate(
            {"name": "x", "type": "connector", "platform": "jira",
             "base_url": "https://x.example.com"}
        )


def test_connector_in_config_with_ref_auth() -> None:
    # a ref-auth connector reuses the test_credentials machinery + its validator
    cfg = PFactoryConfig.model_validate(
        _cfg(
            {
                "name": "snow", "type": "connector", "platform": "servicenow",
                "base_url": "https://acme.service-now.com",
                "auth": {"type": "ref", "ref": "snow-svc"},
            },
            test_credentials={"snow-svc": {"ref": "env:SNOW_TOKEN", "as_secret": "TEST_PASSWORD"}},
        )
    )
    assert type(cfg.targets[0]).__name__ == "ConnectorTarget"


def test_ref_auth_validator_still_fires_for_connector() -> None:
    # auth.ref must name a declared credential — even on a connector target
    with pytest.raises(ValidationError):
        PFactoryConfig.model_validate(
            _cfg({
                "name": "snow", "type": "connector", "platform": "servicenow",
                "base_url": "https://acme.service-now.com",
                "auth": {"type": "ref", "ref": "missing"},
            })
        )


def test_platform_registry_maps_to_guidance() -> None:
    info = connector_platform_info("salesforce")
    assert info and info["api_style"] == "rest"
    assert "SOQL" in info["guidance"]
    assert connector_platform_info("nope") is None


def test_every_platform_template_exists() -> None:
    # the documented pattern: each platform's library template must be a real file
    for platform, info in CONNECTOR_PLATFORMS.items():
        tmpl = info["library_template"]
        if not tmpl:
            continue  # platform whose template is still TBD (e.g. SAP)
        assert (_LIBRARY / tmpl).is_file(), f"{platform} → missing library/{tmpl}"
