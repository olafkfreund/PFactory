"""
pfactory_yml — .pfactory.yml schema, parser, and secret helpers
================================================================

Public API::

    from pfactory_yml import (
        PFactoryConfig,
        Target,
        TargetSpec,
        Auth,
        AuthSpec,
        HealthCheck,
        WaitFor,
        TestData,
        EvidencePolicy,
        HttpTarget,
        KubernetesTarget,
        DockerComposeTarget,
        FeatureFlagTarget,
        BearerAuth,
        BasicAuth,
        OAuth2ClientCredentialsAuth,
        ServiceAccountAuth,
        MtlsAuth,
        NoneAuth,
        load_pfactory_yml,
        PFactoryYmlError,
        resolve_env_var,
        MissingSecretError,
    )
"""

from .exceptions import PFactoryYmlError
from .parser import _has_env_var_references, load_pfactory_yml, load_pfactory_yml_text
from .schema import (
    CONNECTOR_PLATFORMS,
    AuthSpec,
    BasicAuth,
    BearerAuth,
    ConnectorTarget,
    DockerComposeTarget,
    EvidencePolicy,
    FeatureFlagTarget,
    HealthCheck,
    HttpTarget,
    KubernetesTarget,
    MtlsAuth,
    NoneAuth,
    OAuth2ClientCredentialsAuth,
    PFactoryConfig,
    ServiceAccountAuth,
    TargetSpec,
    TestData,
    WaitFor,
    connector_platform_info,
)
from .secrets import MissingSecretError, resolve_auth_env_vars, resolve_env_var

# Legacy aliases used in the task spec
Target = TargetSpec
Auth = AuthSpec

__all__ = [
    # Config root
    "PFactoryConfig",
    # Target types
    "TargetSpec",
    "Target",  # alias
    "HttpTarget",
    "KubernetesTarget",
    "DockerComposeTarget",
    "FeatureFlagTarget",
    "ConnectorTarget",
    "CONNECTOR_PLATFORMS",
    "connector_platform_info",
    # Auth types
    "AuthSpec",
    "Auth",  # alias
    "BearerAuth",
    "BasicAuth",
    "OAuth2ClientCredentialsAuth",
    "ServiceAccountAuth",
    "MtlsAuth",
    "NoneAuth",
    # Supporting models
    "HealthCheck",
    "WaitFor",
    "TestData",
    "EvidencePolicy",
    # Parser
    "load_pfactory_yml",
    "load_pfactory_yml_text",
    "_has_env_var_references",
    "PFactoryYmlError",
    # Secrets
    "resolve_env_var",
    "resolve_auth_env_vars",
    "MissingSecretError",
]
