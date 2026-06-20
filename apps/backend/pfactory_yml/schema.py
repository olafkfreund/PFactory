"""
.pfactory.yml Pydantic v2 Schema
=================================

Defines all models that describe a ``.pfactory.yml`` config file placed at an
AIFactory repo root.  The schema is **parse-only** — environment variable
values are intentionally NOT resolved here.  Auth models store the *name* of
the env-var (e.g. ``token_env: STAGING_API_TOKEN``), not the secret itself,
so the file can be committed to version control and shared in PRs.

Env-var resolution happens at Executor invocation time via
``pfactory_yml.secrets.resolve_env_var()``.

Decision 10 (v0.2 design spec): target addressing via single ``.pfactory.yml``
with a ``targets:`` array; subtasks reference by name.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _validate_env_var_name(name: str, field_name: str) -> str:
    """Raise ValueError if *name* is not a valid env-var identifier."""
    if not _ENV_VAR_RE.match(name):
        raise ValueError(
            f"'{field_name}' must be an uppercase env-var name matching "
            f"[A-Z_][A-Z0-9_]* (got {name!r}).  Store the variable NAME, "
            "not the secret value."
        )
    return name


# ---------------------------------------------------------------------------
# Auth models (discriminated union on 'type')
# ---------------------------------------------------------------------------


class BearerAuth(BaseModel):
    """HTTP Bearer token auth.  The token value is read from *token_env* at
    runtime; only the env-var NAME is stored here."""

    type: Literal["bearer"]
    token_env: str  # env-var name, e.g. STAGING_API_TOKEN

    @field_validator("token_env")
    @classmethod
    def _check_token_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "token_env")


class BasicAuth(BaseModel):
    """HTTP Basic auth.  Credentials are read from env vars at runtime."""

    type: Literal["basic"]
    username_env: str
    password_env: str

    @field_validator("username_env")
    @classmethod
    def _check_username_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "username_env")

    @field_validator("password_env")
    @classmethod
    def _check_password_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "password_env")


class OAuth2ClientCredentialsAuth(BaseModel):
    """OAuth 2.0 client-credentials flow."""

    type: Literal["oauth2_client_credentials"]
    token_url: AnyHttpUrl
    client_id_env: str
    client_secret_env: str
    scopes: list[str] = []

    @field_validator("client_id_env")
    @classmethod
    def _check_client_id_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "client_id_env")

    @field_validator("client_secret_env")
    @classmethod
    def _check_client_secret_env(cls, v: str) -> str:
        return _validate_env_var_name(v, "client_secret_env")


class ServiceAccountAuth(BaseModel):
    """Kubernetes ServiceAccount token file auth."""

    type: Literal["serviceaccount"]
    token_file: str  # path to the mounted token file


class MtlsAuth(BaseModel):
    """Mutual TLS (mTLS) client certificate auth."""

    type: Literal["mtls"]
    client_cert: str  # path to PEM client cert
    client_key: str  # path to PEM private key
    ca_cert: str | None = None  # optional CA bundle path


class NoneAuth(BaseModel):
    """Explicit declaration of no authentication required."""

    type: Literal["none"]


class RefAuth(BaseModel):
    """Auth that references a named ``test_credentials`` entry (#107).

    For a form login (the default), PFactory drives the login page with the
    given selectors and the credential's resolved username/secret, then reuses
    the authenticated session (Playwright ``storageState``). ``ref`` names a
    key in the top-level ``test_credentials`` map; the selectors are consumed
    by the browser-lane login setup.
    """

    type: Literal["ref"]
    ref: str  # a key in the top-level test_credentials map
    login_url: str | None = None
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_url_pattern: str | None = None


# Union type for the ``auth:`` field (discriminated on ``type``).
AuthSpec = Annotated[
    BearerAuth
    | BasicAuth
    | OAuth2ClientCredentialsAuth
    | ServiceAccountAuth
    | MtlsAuth
    | NoneAuth
    | RefAuth,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# HealthCheck + WaitFor
# ---------------------------------------------------------------------------


class HealthCheck(BaseModel):
    """Liveness probe for HTTP-type targets."""

    path: str = "/healthz"
    expect_status: int = 200
    timeout_seconds: int = 10


class WaitFor(BaseModel):
    """Poll a URL until it responds (used with docker_compose targets)."""

    url: str
    timeout_seconds: int = 60
    expect_status: int = 200


# ---------------------------------------------------------------------------
# Target models (discriminated union on 'type')
# ---------------------------------------------------------------------------


class HttpTarget(BaseModel):
    """An HTTP/HTTPS service endpoint (browser app or REST API).

    Used for both Browser-lane targets (the app under test) and API-lane
    targets (REST/GraphQL APIs).  The ``base_url`` is the root URL; the
    Executor appends test-specific paths.

    Examples::

        # Browser lane
        - name: web
          type: http
          base_url: https://staging.example.com
          auth:
            type: bearer
            token_env: STAGING_API_TOKEN
          health_check:
            path: /healthz
            expect_status: 200

        # API lane (with OpenAPI spec for Gen-Functional context)
        - name: api
          type: http
          base_url: https://api.staging.example.com
          openapi_spec: openapi.yaml
          auth:
            type: oauth2_client_credentials
            token_url: https://auth.example.com/token
            client_id_env: API_CLIENT_ID
            client_secret_env: API_CLIENT_SECRET
    """

    type: Literal["http"]
    name: str
    base_url: AnyHttpUrl
    auth: AuthSpec | None = None
    health_check: HealthCheck | None = None
    # Optional hints consumed by the Planner / Gen-Functional
    openapi_spec: str | None = None  # path to OpenAPI spec for API lane
    selectors_hint: str | None = None  # e.g. "data_testid" or "role_based"


class KubernetesTarget(BaseModel):
    """A service accessed via a Kubernetes cluster context.

    The Executor port-forwards to the named service to make it reachable
    locally.  Only ``serviceaccount`` or ``mtls`` auth is permitted — bearer
    tokens and basic auth are not appropriate for in-cluster access.

    Examples::

        - name: cluster
          type: kubernetes
          context: prod-readonly
          namespace: example-app
          auth:
            type: serviceaccount
            token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
    """

    type: Literal["kubernetes"]
    name: str
    context: str
    namespace: str
    # Only ServiceAccount or mTLS auth makes sense for in-cluster access.
    # BearerAuth, BasicAuth, etc. would be misleading here — reject them.
    auth: Annotated[
        ServiceAccountAuth | MtlsAuth,
        Field(discriminator="type"),
    ]
    service: str | None = None
    port: int | None = None
    port_forward: bool = False


class DockerComposeTarget(BaseModel):
    """A set of services spun up via docker-compose for local testing.

    Decision 5 (v0.2): docker-compose is the default runtime for Browser-lane
    tests when a ``base_url`` override is not provided.

    Examples::

        - name: web
          type: docker_compose
          compose_file: docker-compose.test.yml
          services: [app, db, redis]
          wait_for:
            - url: http://localhost:3000/ready
              timeout_seconds: 60
    """

    type: Literal["docker_compose"]
    name: str
    compose_file: str  # path relative to repo root
    services: list[str]  # must contain at least one service name
    wait_for: list[WaitFor] = []

    @field_validator("services")
    @classmethod
    def _require_at_least_one_service(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "docker_compose target 'services' must list at least one "
                "service name (got an empty list)"
            )
        return v


class FeatureFlagTarget(BaseModel):
    """A feature-flag / gate overlay.

    Used for testing rollouts, dark launches, and A/B variants.  The
    Planner injects the flag state so Gen-Functional can write tests that
    assert the correct behaviour for each variant.

    Supported services: ``growthbook``, ``launchdarkly``, ``split``,
    ``unleash``.

    Examples::

        - name: billing-flag
          type: feature_flag
          flag_key: new-billing-flow
          service: launchdarkly
          auth:
            type: bearer
            token_env: LD_SDK_KEY
    """

    type: Literal["feature_flag"]
    name: str
    flag_key: str
    service: Literal["growthbook", "launchdarkly", "split", "unleash"]
    auth: AuthSpec | None = None


class CloudScanConfig(BaseModel):
    """What a cloud discovery/assessment run should do for a target (#133).

    Defaults run a full read-only discovery + CIS misconfiguration assessment
    and fail the run (verdict=reject) on any finding at or above ``high``.
    """

    discover: bool = True  # enumerate resources → inventory + diagram
    misconfiguration: bool = True  # run CIS/OCSF misconfiguration checks
    services: list[str] = []  # restrict to these services (e.g. s3, iam, ec2); [] = all
    compliance: list[str] = ["cis"]  # frameworks to assess against (cis, nist, …)
    # Findings at or above this severity gate the run to verdict=reject.
    fail_on_severity: Literal["critical", "high", "medium", "low"] = "high"


class CloudProviderTarget(BaseModel):
    """A cloud account / subscription / project to discover + assess (#133).

    Read-only by design: PFactory enumerates resources and checks their
    configuration; it never mutates the account. Credentials resolve either
    from an ambient CLI ``profile`` (e.g. an AWS named profile), an optional
    read-only ``assume_role`` (AWS role ARN / GCP impersonated SA / Azure MI),
    or a vault-backed ``auth: { type: ref }`` reference — never inline secrets.

    Requires ``egress.enabled`` (cloud APIs need network) — enforced at the
    config level, like ``test_credentials``.

    Examples::

        - name: aws-prod
          type: cloud_provider
          provider: aws
          regions: [us-east-1, eu-west-2]
          profile: Calitii          # ambient CLI profile
          assume_role: arn:aws:iam::123456789012:role/pfactory-readonly
          scan:
            misconfiguration: true
            compliance: [cis]
            fail_on_severity: high
    """

    type: Literal["cloud_provider"]
    name: str
    provider: Literal["aws", "azure", "gcp"]
    regions: list[str] = []  # [] = provider default / all enabled regions
    profile: str | None = None  # named CLI profile (aws profile / gcloud config / az subscription)
    assume_role: str | None = None  # read-only role ARN / impersonated SA / managed identity
    auth: RefAuth | None = None  # vault-backed credential reference (alternative to profile)
    scan: CloudScanConfig = Field(default_factory=CloudScanConfig)


# Managed-SaaS platforms with a first-class connector profile (#111). Each maps
# to an API style + the pytest `library/` check template + Gen-Functional
# guidance. Adding a platform = add an entry here + a `library/<template>` file
# (the documented pattern — see guides/saas-connectors.md).
CONNECTOR_PLATFORMS: dict[str, dict[str, str]] = {
    "servicenow": {
        "api_style": "rest",  # Table API
        "library_template": "servicenow-table-api.py.tmpl",
        "guidance": (
            "ServiceNow: prefer the Table API (`/api/now/table/<table>`) over UI "
            "automation — SSO-gated portals are brittle. Bearer/OAuth token via the "
            "credential vault; assert on `result[0].<field>`."
        ),
    },
    "salesforce": {
        "api_style": "rest",  # REST + SOQL
        "library_template": "salesforce-rest-query.py.tmpl",
        "guidance": (
            "Salesforce: use the REST API + SOQL (`/services/data/vXX.0/query`) with "
            "an OAuth bearer token from the vault; avoid Lightning DOM automation."
        ),
    },
    "mulesoft": {
        "api_style": "rest",
        "library_template": "mulesoft-api.py.tmpl",
        "guidance": "MuleSoft: drive the published API endpoints directly with a vault token.",
    },
    "sap": {
        "api_style": "odata",  # SAP Gateway / S/4HANA OData v2/v4
        "library_template": "",  # template TBD — see guides/saas-connectors.md
        "guidance": (
            "SAP: drive OData services (`/sap/opu/odata/...`) with `$filter`/`$top`; "
            "auth via the vault (basic / OAuth). API-first over SAP GUI automation."
        ),
    },
}


def connector_platform_info(platform: str) -> dict[str, str] | None:
    """Registry entry (api style · library template · guidance) for a platform."""
    return CONNECTOR_PLATFORMS.get(platform)


class ConnectorTarget(BaseModel):
    """A managed-SaaS platform target — ServiceNow / Salesforce / SAP / MuleSoft (#111).

    A first-class, ergonomic alternative to a raw ``http`` target: name the
    ``platform`` and PFactory knows its API style + which ``library/`` check
    template + Gen-Functional guidance to use. Auth + ``base_url`` reuse the
    HTTP / credential-vault plumbing (``auth: { type: ref }`` resolves an
    OAuth/SSO token from the vault). Tests drive the platform's REST/OData API
    on the **api lane** — API-first is far more stable than SSO-gated browser
    automation.

    Example::

        - name: snow
          type: connector
          platform: servicenow
          base_url: https://acme.service-now.com
          entities: [incident, change_request]
          auth:
            type: ref
            ref: snow-svc
    """

    type: Literal["connector"]
    name: str
    platform: Literal["servicenow", "salesforce", "sap", "mulesoft"]
    base_url: AnyHttpUrl
    auth: AuthSpec | None = None
    health_check: HealthCheck | None = None
    # Tables / objects / OData entity sets to focus generation on (hints).
    entities: list[str] = []


# Union of all concrete target types (discriminated on ``type``).
TargetSpec = Annotated[
    HttpTarget
    | KubernetesTarget
    | DockerComposeTarget
    | FeatureFlagTarget
    | CloudProviderTarget
    | ConnectorTarget,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# TestData
# ---------------------------------------------------------------------------


class TestData(BaseModel):
    """Commands for seeding / resetting the test database."""

    fixtures_dir: str | None = None
    seed_command: str | None = None
    reset_command: str | None = None


# ---------------------------------------------------------------------------
# EvidencePolicy (Task 16 / #32 — concrete fields)
# ---------------------------------------------------------------------------


class EvidenceBrowserPolicy(BaseModel):
    """Screenshot / video / trace settings for the Browser lane (Playwright).

    All fields correspond to Playwright's ``use:`` config options.

    Attributes:
        screenshot: When to capture screenshots.
            ``"always"`` captures after every test; ``"on-failure"``
            captures only when the test fails; ``"never"`` disables.
        video: When to retain video recordings.
            ``"always"`` keeps every run; ``"retain-on-failure"`` deletes
            passing-run videos; ``"never"`` disables.
        trace: When to record and retain Playwright traces.
            ``"always"`` traces every run; ``"on-first-retry"`` starts
            tracing on the first retry (Playwright's default); ``"never"``
            disables.
    """

    screenshot: Literal["always", "on-failure", "never"] = "on-failure"
    video: Literal["always", "retain-on-failure", "never"] = "retain-on-failure"
    trace: Literal["always", "on-first-retry", "never"] = "on-first-retry"


class EvidenceApiPolicy(BaseModel):
    """HTTP recording settings for the API / Integration lanes.

    Attributes:
        record_http: When to record outbound HTTP calls to a ``.har`` file.
            ``"always"`` records every run; ``"on-failure"`` records only
            when the test fails; ``"never"`` disables recording.
    """

    record_http: Literal["always", "on-failure", "never"] = "on-failure"


class EvidenceRetentionPolicy(BaseModel):
    """Evidence retention windows per verdict bucket.

    Attributes:
        failures: How long to keep evidence for *failed* tests.
            ``"forever"`` keeps indefinitely; ``"<N>_days"`` keeps for
            N calendar days (e.g. ``"30_days"``).
        flagged: How long to keep evidence for *flagged* tests.
        passing: How long to keep evidence for *passing* tests.
        size_cap_per_task: Maximum total evidence size per spec_id.
            Format: ``"<N>MB"`` or ``"<N>GB"`` (e.g. ``"500MB"``).
            ``None`` disables the cap.
    """

    failures: str = "forever"
    flagged: str = "90_days"
    passing: str = "7_days"
    size_cap_per_task: str | None = "500MB"

    @field_validator("failures", "flagged", "passing")
    @classmethod
    def _validate_retention_window(cls, v: str) -> str:
        if v == "forever":
            return v
        if v.endswith("_days"):
            days_part = v[: -len("_days")]
            try:
                n = int(days_part)
                if n < 1:
                    raise ValueError("must be a positive integer")
                return v
            except ValueError:
                pass
        raise ValueError(f"retention window must be 'forever' or '<N>_days' (got {v!r})")

    @field_validator("size_cap_per_task")
    @classmethod
    def _validate_size_cap(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        for suffix in ("GB", "MB", "KB"):
            if v.endswith(suffix):
                num_part = v[: -len(suffix)].strip()
                try:
                    n = float(num_part)
                    if n <= 0:
                        raise ValueError("size must be positive")
                    return v
                except ValueError:
                    pass
        raise ValueError(f"size_cap_per_task must be '<N>MB' or '<N>GB' (got {v!r})")


class EvidencePolicy(BaseModel):
    """Evidence-capture policy (screenshots, video, trace, HAR).

    Implemented in Task 16 / #32.  Extends the placeholder with concrete
    typed sub-models while retaining ``extra="allow"`` for forward
    compatibility.

    Example ``.pfactory.yml`` stanza::

        evidence_policy:
          browser:
            screenshot: on-failure
            video: retain-on-failure
            trace: on-first-retry
          api:
            record_http: always
          retention:
            failures: forever
            flagged: 90_days
            passing: 7_days
            size_cap_per_task: 500MB
    """

    model_config = {"extra": "allow"}

    browser: EvidenceBrowserPolicy = Field(default_factory=EvidenceBrowserPolicy)
    api: EvidenceApiPolicy = Field(default_factory=EvidenceApiPolicy)
    retention: EvidenceRetentionPolicy = Field(default_factory=EvidenceRetentionPolicy)


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class EgressDestination(BaseModel):
    """A declared network destination credentials are allowed to reach."""

    name: str
    host: str  # hostname or glob (e.g. "api.staging.example.com", "*.googleapis.com")
    description: str | None = None


class EgressConfig(BaseModel):
    """Honest-egress gate (epic #62). Default OFF — no cloud credentials are
    resolved unless ``enabled`` is true."""

    enabled: bool = False
    destinations: list[EgressDestination] = Field(default_factory=list)


class CredentialEntry(BaseModel):
    """One named credential: a backend ref + how to expose it to consumers.

    ``ref`` is a secret reference (e.g. ``vault:secret/data/app#token``,
    ``gcp-sm://proj/sa``); ``as`` is the env-var name to set; ``kind=file``
    writes the value to a 0600 file and sets the env var to that path.
    """

    ref: str
    as_: str = Field(alias="as")
    kind: Literal["env", "file"] = "env"

    model_config = {"populate_by_name": True}


class TestCredentialEntry(BaseModel):
    """A named test-target credential (#107): a secret ref + how to expose it.

    Resolved per run by ``resolve_test_target_credentials`` into ephemeral
    sandbox env. ``ref`` is a secret reference — ``store:<id>`` (resolved
    web-server-side, since the backend agent has no DB driver) or
    ``env:``/``vault:`` (resolved by the broker). The resolved secret becomes
    the ``as_secret`` env var; an optional ``username_ref`` resolves the
    plaintext username into ``as_username``.
    """

    ref: str
    as_secret: str
    as_username: str | None = None
    username_ref: str | None = None
    kind: Literal["form", "api_token", "basic_auth", "totp"] = "form"

    @field_validator("as_secret", "as_username")
    @classmethod
    def _check_env_names(cls, v: str | None) -> str | None:
        return v if v is None else _validate_env_var_name(v, "as_secret/as_username")


class PFactoryConfig(BaseModel):
    """Parsed representation of a ``.pfactory.yml`` file.

    Example::

        version: 1
        targets:
          - name: api
            type: http
            base_url: https://api.staging.example.com
            auth:
              type: bearer
              token_env: STAGING_API_TOKEN
        test_data:
          seed_command: ./scripts/seed-test-db.sh
    """

    version: Literal[1]
    targets: list[TargetSpec]
    default_target: str | None = None
    test_data: TestData | None = None
    evidence_policy: EvidencePolicy | None = None
    # Optional path overrides (consumed by Planner / Gen-Functional)
    test_paths: dict[str, str] | None = None
    # Credential broker (epic #62): egress gate + named credential refs.
    egress: EgressConfig = Field(default_factory=EgressConfig)
    credentials: dict[str, CredentialEntry] | None = None
    # Test-target login credentials (#107): name → secret ref + env mapping.
    test_credentials: dict[str, TestCredentialEntry] | None = None

    @model_validator(mode="after")
    def _validate_test_credentials(self) -> PFactoryConfig:
        """Fail closed on test-target auth misconfig (#107).

        - declaring ``test_credentials`` requires egress (login needs network),
        - every ``auth: {type: ref}`` must name a declared credential.
        """
        tc = self.test_credentials or {}
        if tc and not self.egress.enabled:
            raise ValueError(
                "test_credentials is set but egress.enabled is false. "
                "Test-target login needs network egress — set egress.enabled: true."
            )
        for target in self.targets:
            auth = getattr(target, "auth", None)
            if auth is not None and getattr(auth, "type", None) == "ref":
                if auth.ref not in tc:
                    raise ValueError(
                        f"target {target.name!r} auth.ref {auth.ref!r} does not "
                        f"match any test_credentials entry; known: {sorted(tc)}"
                    )
        return self

    @model_validator(mode="after")
    def _validate_cloud_targets(self) -> PFactoryConfig:
        """Fail closed on cloud target misconfig (#133).

        A ``cloud_provider`` target needs network egress to reach cloud APIs,
        so declaring one without ``egress.enabled`` is rejected.
        """
        cloud = [t for t in self.targets if getattr(t, "type", None) == "cloud_provider"]
        if cloud and not self.egress.enabled:
            names = sorted(t.name for t in cloud)
            raise ValueError(
                f"cloud_provider target(s) {names} require network egress to "
                "reach cloud APIs — set egress.enabled: true."
            )
        return self

    @model_validator(mode="after")
    def _validate_default_target(self) -> PFactoryConfig:
        """Ensure default_target (if set) refers to a declared target name."""
        if self.default_target is not None:
            names = {t.name for t in self.targets}
            if self.default_target not in names:
                raise ValueError(
                    f"default_target {self.default_target!r} does not match "
                    f"any declared target name; known: {sorted(names)}"
                )
        return self

    # -------------------------------------------------------------------------
    # Convenience helpers (consumed by Planner, Gen-Functional, Executor)
    # -------------------------------------------------------------------------

    def target_names(self) -> list[str]:
        """Return sorted list of all declared target names."""
        return sorted(t.name for t in self.targets)

    def lookup_target(self, name: str) -> TargetSpec | None:
        """Return the target with *name*, or ``None`` if not found.

        Parameters
        ----------
        name:
            The ``name`` field of the target to find.

        Returns
        -------
        TargetSpec | None
            The matching target, or ``None``.
        """
        for target in self.targets:
            if target.name == name:
                return target
        return None

    def get_target(self, name: str) -> TargetSpec:
        """Return the target with *name*, raising ``KeyError`` if not found.

        Parameters
        ----------
        name:
            The ``name`` field of the target to find.

        Raises
        ------
        KeyError
            If no target with *name* is declared.
        """
        target = self.lookup_target(name)
        if target is None:
            raise KeyError(
                f"Target {name!r} not found in .pfactory.yml. "
                f"Declared targets: {self.target_names()}"
            )
        return target
