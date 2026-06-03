"""
Test suite for apps/backend/pfactory_yml — Task 2 (#18).

Coverage targets per the task spec:
  - Round-trip for each of 4 target types (parse + model_dump)
  - Missing-field rejection per target type
  - Auth type discrimination: 6 cases parse correctly; nonsense rejected
  - Env-var name validation: bad patterns rejected, good patterns accepted
  - Env-var indirection NOT resolved at parse time
  - resolve_env_var raises MissingSecretError when env var is unset
  - load_pfactory_yml returns None for missing file
  - load_pfactory_yml returns valid PFactoryConfig for an example file
  - Malformed YAML → PFactoryYmlError with path context
  - Unknown target type → useful error message
  - KubernetesTarget rejects BearerAuth (only serviceaccount/mtls allowed)
  - DockerComposeTarget rejects services: []
  - WaitFor list defaults to empty
  - TestData all-fields-None parses cleanly
  - TestData with seed+reset commands parses
  - version=2 rejected (only version=1 known at v0.2)
  - .pfactory.yml.example at repo root parses with 4 targets
  - PFactoryConfig helpers: target_names(), lookup_target(), get_target()
  - load_pfactory_yml_text() for in-memory parsing
  - resolve_auth_env_vars() resolves all *_env fields at runtime

Total: 35+ parametrized and standalone test functions.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
from pfactory_yml import (
    BasicAuth,
    BearerAuth,
    DockerComposeTarget,
    FeatureFlagTarget,
    HealthCheck,
    HttpTarget,
    KubernetesTarget,
    MissingSecretError,
    MtlsAuth,
    NoneAuth,
    OAuth2ClientCredentialsAuth,
    PFactoryConfig,
    PFactoryYmlError,
    ServiceAccountAuth,
    TestData,
    WaitFor,
    _has_env_var_references,
    load_pfactory_yml,
    load_pfactory_yml_text,
    resolve_auth_env_vars,
    resolve_env_var,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yml(tmp_path: Path, content: str) -> Path:
    """Write content to .pfactory.yml in tmp_path and return the path."""
    path = tmp_path / ".pfactory.yml"
    path.write_text(textwrap.dedent(content))
    return tmp_path  # return the dir (repo root), not the file


# ---------------------------------------------------------------------------
# 1. Round-trip for each target type
# ---------------------------------------------------------------------------

def test_http_target_round_trip():
    """HttpTarget parses from raw dict and round-trips via model_dump."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "http",
                "name": "api",
                "base_url": "https://api.example.com",
                "auth": {"type": "bearer", "token_env": "API_TOKEN"},
                "health_check": {"path": "/health", "expect_status": 200},
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert len(cfg.targets) == 1
    target = cfg.targets[0]
    assert isinstance(target, HttpTarget)
    assert target.name == "api"
    assert str(target.base_url).rstrip("/") == "https://api.example.com"
    assert target.auth is not None
    assert isinstance(target.auth, BearerAuth)
    assert target.auth.token_env == "API_TOKEN"
    # Round-trip
    dumped = cfg.model_dump()
    cfg2 = PFactoryConfig.model_validate(dumped)
    assert cfg2.targets[0].name == "api"


def test_kubernetes_target_round_trip():
    """KubernetesTarget parses and round-trips."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "kubernetes",
                "name": "cluster",
                "context": "prod-readonly",
                "namespace": "example-app",
                "auth": {
                    "type": "serviceaccount",
                    "token_file": "/var/run/secrets/kubernetes.io/serviceaccount/token",
                },
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    t = cfg.targets[0]
    assert isinstance(t, KubernetesTarget)
    assert t.context == "prod-readonly"
    assert t.namespace == "example-app"
    assert isinstance(t.auth, ServiceAccountAuth)
    assert t.auth.token_file == "/var/run/secrets/kubernetes.io/serviceaccount/token"
    # Round-trip
    cfg2 = PFactoryConfig.model_validate(cfg.model_dump())
    assert cfg2.targets[0].namespace == "example-app"


def test_docker_compose_target_round_trip():
    """DockerComposeTarget parses and round-trips."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "docker_compose",
                "name": "web",
                "compose_file": "docker-compose.test.yml",
                "services": ["app", "db", "redis"],
                "wait_for": [
                    {"url": "http://localhost:3000/ready", "timeout_seconds": 60}
                ],
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    t = cfg.targets[0]
    assert isinstance(t, DockerComposeTarget)
    assert t.compose_file == "docker-compose.test.yml"
    assert t.services == ["app", "db", "redis"]
    assert len(t.wait_for) == 1
    assert t.wait_for[0].timeout_seconds == 60
    cfg2 = PFactoryConfig.model_validate(cfg.model_dump())
    assert cfg2.targets[0].services == ["app", "db", "redis"]


def test_feature_flag_target_round_trip():
    """FeatureFlagTarget parses and round-trips."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "feature_flag",
                "name": "billing",
                "flag_key": "new-billing-flow",
                "service": "launchdarkly",
                "auth": {"type": "bearer", "token_env": "LD_SDK_KEY"},
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    t = cfg.targets[0]
    assert isinstance(t, FeatureFlagTarget)
    assert t.flag_key == "new-billing-flow"
    assert t.service == "launchdarkly"
    cfg2 = PFactoryConfig.model_validate(cfg.model_dump())
    assert cfg2.targets[0].service == "launchdarkly"


# ---------------------------------------------------------------------------
# 2. Missing required fields — one test per target type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_field,payload", [
    ("base_url", {"type": "http", "name": "api"}),
    ("name",     {"type": "http", "base_url": "https://example.com"}),
])
def test_http_target_missing_required(missing_field, payload):
    """HttpTarget rejects when a required field is absent."""
    with pytest.raises(Exception):  # pydantic ValidationError or PFactoryYmlError
        PFactoryConfig.model_validate({"version": 1, "targets": [payload]})


@pytest.mark.parametrize("missing_field,payload", [
    ("context",   {"type": "kubernetes", "name": "k", "namespace": "ns",
                   "auth": {"type": "serviceaccount", "token_file": "/t"}}),
    ("namespace", {"type": "kubernetes", "name": "k", "context": "ctx",
                   "auth": {"type": "serviceaccount", "token_file": "/t"}}),
    ("auth",      {"type": "kubernetes", "name": "k", "context": "ctx", "namespace": "ns"}),
])
def test_kubernetes_target_missing_required(missing_field, payload):
    """KubernetesTarget rejects when a required field is absent."""
    with pytest.raises(Exception):
        PFactoryConfig.model_validate({"version": 1, "targets": [payload]})


@pytest.mark.parametrize("missing_field,payload", [
    ("compose_file", {"type": "docker_compose", "name": "w", "services": ["app"]}),
    ("services",     {"type": "docker_compose", "name": "w",
                      "compose_file": "docker-compose.yml"}),
    ("name",         {"type": "docker_compose", "compose_file": "dc.yml",
                      "services": ["app"]}),
])
def test_docker_compose_target_missing_required(missing_field, payload):
    """DockerComposeTarget rejects when a required field is absent."""
    with pytest.raises(Exception):
        PFactoryConfig.model_validate({"version": 1, "targets": [payload]})


@pytest.mark.parametrize("missing_field,payload", [
    ("flag_key", {"type": "feature_flag", "name": "f", "service": "growthbook"}),
    ("service",  {"type": "feature_flag", "name": "f", "flag_key": "k"}),
    ("name",     {"type": "feature_flag", "flag_key": "k", "service": "split"}),
])
def test_feature_flag_target_missing_required(missing_field, payload):
    """FeatureFlagTarget rejects when a required field is absent."""
    with pytest.raises(Exception):
        PFactoryConfig.model_validate({"version": 1, "targets": [payload]})


# ---------------------------------------------------------------------------
# 3. Auth type discrimination
# ---------------------------------------------------------------------------

_BEARER = {"type": "bearer", "token_env": "MY_TOKEN"}
_BASIC = {"type": "basic", "username_env": "MY_USER", "password_env": "MY_PASS"}
_OAUTH2 = {
    "type": "oauth2_client_credentials",
    "token_url": "https://auth.example.com/token",
    "client_id_env": "CLIENT_ID",
    "client_secret_env": "CLIENT_SECRET",
}
_SERVICEACCOUNT = {"type": "serviceaccount", "token_file": "/token"}
_MTLS = {"type": "mtls", "client_cert": "/cert.pem", "client_key": "/key.pem"}
_NONE = {"type": "none"}


@pytest.mark.parametrize("auth_data,expected_type", [
    (_BEARER, BearerAuth),
    (_BASIC, BasicAuth),
    (_OAUTH2, OAuth2ClientCredentialsAuth),
    (_SERVICEACCOUNT, ServiceAccountAuth),
    (_MTLS, MtlsAuth),
    (_NONE, NoneAuth),
])
def test_auth_types_discriminated(auth_data, expected_type):
    """All six auth types parse correctly via the discriminated union."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com",
             "auth": auth_data}
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert isinstance(cfg.targets[0].auth, expected_type)


def test_auth_type_nonsense_rejected():
    """An unknown auth type is rejected with a useful error."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com",
             "auth": {"type": "nonsense_auth", "token": "abc"}}
        ],
    }
    with pytest.raises(Exception) as exc_info:
        PFactoryConfig.model_validate(raw)
    err_str = str(exc_info.value)
    assert "nonsense_auth" in err_str or "auth" in err_str.lower()


# ---------------------------------------------------------------------------
# 4. Env-var name validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_name", [
    "not_uppercase",
    "0LEADING_NUM",
    "has space",
    "has-dash",
    "",
])
def test_env_var_name_invalid(bad_name):
    """Bad env-var names are rejected at parse time."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com",
             "auth": {"type": "bearer", "token_env": bad_name}}
        ],
    }
    with pytest.raises(Exception) as exc_info:
        PFactoryConfig.model_validate(raw)
    assert "token_env" in str(exc_info.value) or "env" in str(exc_info.value).lower()


@pytest.mark.parametrize("good_name", [
    "MY_TOKEN",
    "STAGING_API_TOKEN",
    "GOOD_NAME_1",
    "A",
    "_UNDERSCORE_START",
    "TOKEN123",
])
def test_env_var_name_valid(good_name):
    """Valid env-var names are accepted."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com",
             "auth": {"type": "bearer", "token_env": good_name}}
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.targets[0].auth.token_env == good_name


# ---------------------------------------------------------------------------
# 5. Env-var indirection NOT resolved at parse time
# ---------------------------------------------------------------------------

def test_parse_does_not_resolve_env_vars(monkeypatch):
    """Parsing succeeds even when the referenced env var is not set."""
    monkeypatch.delenv("NONEXISTENT_VAR_THAT_DOESNT_EXIST", raising=False)
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com",
             "auth": {"type": "bearer", "token_env": "NONEXISTENT_VAR_THAT_DOESNT_EXIST"}}
        ],
    }
    # Should not raise — only the name is stored, not the value
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.targets[0].auth.token_env == "NONEXISTENT_VAR_THAT_DOESNT_EXIST"


def test_resolve_env_var_raises_missing_secret(monkeypatch):
    """resolve_env_var raises MissingSecretError for unset env vars."""
    monkeypatch.delenv("TOTALLY_ABSENT_VAR", raising=False)
    with pytest.raises(MissingSecretError) as exc_info:
        resolve_env_var("TOTALLY_ABSENT_VAR")
    assert exc_info.value.env_var_name == "TOTALLY_ABSENT_VAR"


def test_resolve_env_var_returns_value(monkeypatch):
    """resolve_env_var returns the value when the env var is set."""
    monkeypatch.setenv("MY_RUNTIME_TOKEN", "secret123")
    assert resolve_env_var("MY_RUNTIME_TOKEN") == "secret123"


def test_resolve_auth_env_vars_bearer(monkeypatch):
    """resolve_auth_env_vars resolves bearer token_env at runtime."""
    monkeypatch.setenv("STAGING_API_TOKEN", "ghp_runtime_value")
    auth = BearerAuth(type="bearer", token_env="STAGING_API_TOKEN")
    resolved = resolve_auth_env_vars(auth)
    assert resolved == {"STAGING_API_TOKEN": "ghp_runtime_value"}


def test_resolve_auth_env_vars_basic(monkeypatch):
    """resolve_auth_env_vars resolves both username and password env vars."""
    monkeypatch.setenv("MY_USER", "userval")
    monkeypatch.setenv("MY_PASS", "passval")
    auth = BasicAuth(type="basic", username_env="MY_USER", password_env="MY_PASS")
    resolved = resolve_auth_env_vars(auth)
    assert resolved == {"MY_USER": "userval", "MY_PASS": "passval"}


def test_resolve_auth_env_vars_none_has_no_secrets():
    """NoneAuth returns an empty dict — no env vars to resolve."""
    auth = NoneAuth(type="none")
    assert resolve_auth_env_vars(auth) == {}


def test_resolve_auth_env_vars_missing_raises(monkeypatch):
    """resolve_auth_env_vars raises MissingSecretError for absent env vars."""
    monkeypatch.delenv("ABSENT_TOKEN", raising=False)
    auth = BearerAuth(type="bearer", token_env="ABSENT_TOKEN")
    with pytest.raises(MissingSecretError) as exc_info:
        resolve_auth_env_vars(auth)
    assert exc_info.value.env_var_name == "ABSENT_TOKEN"


# ---------------------------------------------------------------------------
# 6. load_pfactory_yml filesystem behaviour
# ---------------------------------------------------------------------------

def test_load_returns_none_for_missing_file(tmp_path):
    """load_pfactory_yml returns None when .pfactory.yml is not present."""
    result = load_pfactory_yml(tmp_path)
    assert result is None


def test_load_returns_config_for_valid_file(tmp_path):
    """load_pfactory_yml returns PFactoryConfig for a valid file."""
    _write_yml(tmp_path, """
        version: 1
        targets:
          - name: api
            type: http
            base_url: https://api.example.com
    """)
    cfg = load_pfactory_yml(tmp_path)
    assert cfg is not None
    assert isinstance(cfg, PFactoryConfig)
    assert cfg.targets[0].name == "api"


def test_load_raises_on_malformed_yaml(tmp_path):
    """load_pfactory_yml raises PFactoryYmlError for YAML syntax errors."""
    bad = tmp_path / ".pfactory.yml"
    bad.write_text("version: 1\ntargets:\n  - name: [unclosed bracket")
    with pytest.raises(PFactoryYmlError) as exc_info:
        load_pfactory_yml(tmp_path)
    assert str(tmp_path) in str(exc_info.value) or ".pfactory.yml" in str(exc_info.value)


def test_load_raises_on_validation_error(tmp_path):
    """load_pfactory_yml raises PFactoryYmlError on Pydantic validation failures."""
    _write_yml(tmp_path, """
        version: 1
        targets:
          - name: api
            type: http
            # base_url is required but missing
    """)
    with pytest.raises(PFactoryYmlError) as exc_info:
        load_pfactory_yml(tmp_path)
    assert exc_info.value.path == tmp_path / ".pfactory.yml"
    assert len(exc_info.value.errors) > 0


def test_load_pfactory_yml_text_valid():
    """load_pfactory_yml_text parses valid in-memory YAML."""
    text = """
version: 1
targets:
  - name: api
    type: http
    base_url: https://api.example.com
"""
    cfg = load_pfactory_yml_text(text)
    assert cfg.targets[0].name == "api"


def test_load_pfactory_yml_text_invalid_raises():
    """load_pfactory_yml_text raises PFactoryYmlError for invalid YAML."""
    with pytest.raises(PFactoryYmlError):
        load_pfactory_yml_text(": invalid yaml [{{}")


# ---------------------------------------------------------------------------
# 7. Discriminated union edge cases
# ---------------------------------------------------------------------------

def test_unknown_target_type_rejected():
    """An unknown target type is rejected with a useful error message."""
    raw = {
        "version": 1,
        "targets": [{"type": "unknown_target_type", "name": "bad"}],
    }
    with pytest.raises(Exception) as exc_info:
        PFactoryConfig.model_validate(raw)
    err = str(exc_info.value)
    # Should mention the offending field or the type value
    assert "type" in err.lower() or "unknown_target_type" in err


def test_kubernetes_rejects_bearer_auth():
    """KubernetesTarget rejects BearerAuth — only serviceaccount/mtls allowed."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "kubernetes",
                "name": "k",
                "context": "prod",
                "namespace": "ns",
                "auth": {"type": "bearer", "token_env": "MY_TOKEN"},
            }
        ],
    }
    with pytest.raises(Exception):
        PFactoryConfig.model_validate(raw)


def test_kubernetes_accepts_mtls_auth():
    """KubernetesTarget accepts MtlsAuth."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "kubernetes",
                "name": "k",
                "context": "prod",
                "namespace": "ns",
                "auth": {
                    "type": "mtls",
                    "client_cert": "/cert.pem",
                    "client_key": "/key.pem",
                },
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert isinstance(cfg.targets[0].auth, MtlsAuth)


def test_docker_compose_rejects_empty_services():
    """DockerComposeTarget rejects services: [] (at least one required)."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "docker_compose",
                "name": "w",
                "compose_file": "dc.yml",
                "services": [],
            }
        ],
    }
    with pytest.raises(Exception) as exc_info:
        PFactoryConfig.model_validate(raw)
    assert "service" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 8. Default / optional field behaviour
# ---------------------------------------------------------------------------

def test_wait_for_defaults_to_empty_list():
    """DockerComposeTarget.wait_for defaults to []."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "docker_compose", "name": "w", "compose_file": "dc.yml",
             "services": ["app"]}
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.targets[0].wait_for == []


def test_test_data_all_none_parses():
    """TestData with all fields None parses cleanly."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com"}
        ],
        "test_data": {},
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.test_data is not None
    assert cfg.test_data.fixtures_dir is None
    assert cfg.test_data.seed_command is None
    assert cfg.test_data.reset_command is None


def test_test_data_with_commands():
    """TestData with seed and reset commands parses."""
    raw = {
        "version": 1,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com"}
        ],
        "test_data": {
            "fixtures_dir": "tests/fixtures",
            "seed_command": "./scripts/seed.sh",
            "reset_command": "./scripts/reset.sh",
        },
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.test_data.seed_command == "./scripts/seed.sh"
    assert cfg.test_data.reset_command == "./scripts/reset.sh"
    assert cfg.test_data.fixtures_dir == "tests/fixtures"


def test_version_2_rejected():
    """version: 2 is rejected — only version: 1 is supported at v0.2."""
    raw = {
        "version": 2,
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com"}
        ],
    }
    with pytest.raises(Exception) as exc_info:
        PFactoryConfig.model_validate(raw)
    # Should mention the version field
    assert "version" in str(exc_info.value).lower() or "1" in str(exc_info.value)


def test_health_check_defaults():
    """HealthCheck fields have documented defaults."""
    hc = HealthCheck()
    assert hc.path == "/healthz"
    assert hc.expect_status == 200
    assert hc.timeout_seconds == 10


def test_wait_for_defaults():
    """WaitFor fields have documented defaults."""
    wf = WaitFor(url="http://localhost:3000")
    assert wf.timeout_seconds == 60
    assert wf.expect_status == 200


# ---------------------------------------------------------------------------
# 9. PFactoryConfig helpers
# ---------------------------------------------------------------------------

_TWO_TARGET_CONFIG = {
    "version": 1,
    "targets": [
        {"type": "http", "name": "api", "base_url": "https://api.example.com"},
        {"type": "docker_compose", "name": "web", "compose_file": "dc.yml",
         "services": ["app"]},
    ],
}


def test_target_names_sorted():
    """PFactoryConfig.target_names() returns sorted list of names."""
    cfg = PFactoryConfig.model_validate(_TWO_TARGET_CONFIG)
    assert cfg.target_names() == ["api", "web"]


def test_lookup_target_found():
    """PFactoryConfig.lookup_target() returns the matching target."""
    cfg = PFactoryConfig.model_validate(_TWO_TARGET_CONFIG)
    target = cfg.lookup_target("api")
    assert target is not None
    assert target.name == "api"
    assert isinstance(target, HttpTarget)


def test_lookup_target_not_found():
    """PFactoryConfig.lookup_target() returns None for unknown names."""
    cfg = PFactoryConfig.model_validate(_TWO_TARGET_CONFIG)
    assert cfg.lookup_target("nonexistent") is None


def test_get_target_raises_key_error():
    """PFactoryConfig.get_target() raises KeyError for unknown names."""
    cfg = PFactoryConfig.model_validate(_TWO_TARGET_CONFIG)
    with pytest.raises(KeyError) as exc_info:
        cfg.get_target("nonexistent")
    assert "nonexistent" in str(exc_info.value)


def test_default_target_valid():
    """default_target pointing to a declared target passes validation."""
    raw = {
        "version": 1,
        "default_target": "api",
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com"}
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.default_target == "api"


def test_default_target_invalid_rejected():
    """default_target pointing to an unknown target is rejected."""
    raw = {
        "version": 1,
        "default_target": "nonexistent",
        "targets": [
            {"type": "http", "name": "api", "base_url": "https://api.example.com"}
        ],
    }
    with pytest.raises(Exception) as exc_info:
        PFactoryConfig.model_validate(raw)
    assert "default_target" in str(exc_info.value) or "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 10. has_env_var_references helper
# ---------------------------------------------------------------------------

def test_has_env_var_references_true():
    """_has_env_var_references detects *_env keys."""
    raw = {"targets": [{"auth": {"type": "bearer", "token_env": "MY_TOKEN"}}]}
    assert _has_env_var_references(raw) is True


def test_has_env_var_references_false():
    """_has_env_var_references returns False when there are no *_env keys."""
    raw = {"version": 1, "targets": [{"type": "http", "name": "t",
                                       "base_url": "https://example.com"}]}
    assert _has_env_var_references(raw) is False


# ---------------------------------------------------------------------------
# 11. .pfactory.yml.example parses with 4 targets
# ---------------------------------------------------------------------------

def test_example_file_parses(tmp_path):
    """The .pfactory.yml.example in the repo root parses with 4 targets."""
    # Locate repo root (2 levels up from tests/)
    tests_dir = Path(__file__).parent
    repo_root = tests_dir.parent
    example_path = repo_root / ".pfactory.yml.example"

    if not example_path.exists():
        pytest.skip(".pfactory.yml.example not yet created (created in commit 6)")

    import yaml
    raw_text = example_path.read_text(encoding="utf-8")
    # Remove YAML comment-only lines that might trip the parser
    raw_data = yaml.safe_load(raw_text)
    cfg = PFactoryConfig.model_validate(raw_data)

    assert len(cfg.targets) == 4, (
        f"Expected 4 targets in .pfactory.yml.example, got {len(cfg.targets)}: "
        f"{cfg.target_names()}"
    )
    # One of each type
    types = {t.type for t in cfg.targets}
    assert "http" in types
    assert "kubernetes" in types
    assert "docker_compose" in types
    assert "feature_flag" in types


# ---------------------------------------------------------------------------
# 12. OAuth2 auth round-trip
# ---------------------------------------------------------------------------

def test_oauth2_auth_round_trip():
    """OAuth2ClientCredentialsAuth parses and round-trips."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "http",
                "name": "api",
                "base_url": "https://api.example.com",
                "auth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://auth.example.com/oauth/token",
                    "client_id_env": "API_CLIENT_ID",
                    "client_secret_env": "API_CLIENT_SECRET",
                    "scopes": ["read:data", "write:data"],
                },
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    auth = cfg.targets[0].auth
    assert isinstance(auth, OAuth2ClientCredentialsAuth)
    assert auth.client_id_env == "API_CLIENT_ID"
    assert auth.scopes == ["read:data", "write:data"]
    # Round-trip
    cfg2 = PFactoryConfig.model_validate(cfg.model_dump())
    assert cfg2.targets[0].auth.scopes == ["read:data", "write:data"]


def test_oauth2_invalid_client_id_env_rejected():
    """OAuth2ClientCredentialsAuth rejects non-uppercase client_id_env."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "http",
                "name": "api",
                "base_url": "https://api.example.com",
                "auth": {
                    "type": "oauth2_client_credentials",
                    "token_url": "https://auth.example.com/token",
                    "client_id_env": "not_uppercase_env",
                    "client_secret_env": "SECRET",
                },
            }
        ],
    }
    with pytest.raises(Exception):
        PFactoryConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# 13. Mtls auth optional CA cert
# ---------------------------------------------------------------------------

def test_mtls_auth_with_ca_cert():
    """MtlsAuth accepts optional ca_cert."""
    raw = {
        "version": 1,
        "targets": [
            {
                "type": "kubernetes",
                "name": "k",
                "context": "ctx",
                "namespace": "ns",
                "auth": {
                    "type": "mtls",
                    "client_cert": "/cert.pem",
                    "client_key": "/key.pem",
                    "ca_cert": "/ca.pem",
                },
            }
        ],
    }
    cfg = PFactoryConfig.model_validate(raw)
    assert cfg.targets[0].auth.ca_cert == "/ca.pem"


def test_mtls_auth_without_ca_cert():
    """MtlsAuth works without ca_cert."""
    auth = MtlsAuth(type="mtls", client_cert="/c.pem", client_key="/k.pem")
    assert auth.ca_cert is None
