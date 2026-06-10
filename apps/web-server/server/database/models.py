"""
SQLAlchemy ORM models for the PFactory multi-user system.

All models use SQLAlchemy 2.x declarative style with Mapped columns.
UUIDs are stored as strings since SQLite lacks native UUID support.
Timestamps use server-side defaults via ``func.now()``.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Re-export under a private alias so the model definitions read cleanly
# while making it obvious this is the encrypted-at-rest column type
# (Epic #26 P2). See apps/web-server/server/crypto/.
from ..crypto.encrypted_string import EncryptedString as _EncryptedString


def _generate_uuid() -> str:
    """Generate a new UUID4 string for use as a primary key."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class User(Base):
    """Application user account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Epic #26 P3.3 — Stable OIDC subject identifier. Set on first
    # successful OIDC login (JIT-provisioned). Nullable so that
    # locally-registered users (no SSO) don't need it; unique so that
    # the same IdP user can't accidentally collide across logins.
    oidc_sub: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    # Epic #26 P5.5 — GDPR right-to-erasure timestamp. When set, PII
    # columns (email, name, OAuth tokens) MUST be NULL. Used by the
    # admin UI to render "Erased on YYYY-MM-DD" placeholders instead
    # of treating the user row as deleted. The audit chain preserves
    # historical user_id references via SHA-256 hashing.
    gdpr_erased_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owned_organizations: Mapped[list["Organization"]] = relationship(
        "Organization",
        back_populates="owner",
        foreign_keys="Organization.owner_id",
    )
    org_memberships: Mapped[list["OrgMember"]] = relationship(
        "OrgMember",
        back_populates="user",
        foreign_keys="OrgMember.user_id",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey", back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r}>"


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


class Organization(Base):
    """Organization (team/workspace) that owns projects."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owner: Mapped["User"] = relationship(
        "User",
        back_populates="owned_organizations",
        foreign_keys=[owner_id],
    )
    members: Mapped[list["OrgMember"]] = relationship(
        "OrgMember", back_populates="organization"
    )
    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="organization"
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey", back_populates="organization"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id!r} slug={self.slug!r}>"


# ---------------------------------------------------------------------------
# Organization Members (join table with role)
# ---------------------------------------------------------------------------


class OrgMember(Base):
    """Membership linking a user to an organization with a specific role."""

    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="member"
    )  # owner | admin | member | viewer
    invited_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="members"
    )
    user: Mapped["User"] = relationship(
        "User",
        back_populates="org_memberships",
        foreign_keys=[user_id],
    )
    inviter: Mapped["User | None"] = relationship(
        "User", foreign_keys=[invited_by]
    )

    def __repr__(self) -> str:
        return (
            f"<OrgMember org_id={self.org_id!r} "
            f"user_id={self.user_id!r} role={self.role!r}>"
        )


# ---------------------------------------------------------------------------
# OIDC Refresh Sessions (Epic #26 P3.4)
# ---------------------------------------------------------------------------


class OidcRefreshSession(Base):
    """Per-refresh-token session for OIDC-authenticated users.

    Created when a user completes OIDC login (P3.1) and tracks the
    refresh path's IdP revalidation cadence (P3.4). The row is deleted
    when the user logs out (P3.5) or when the IdP rejects a refresh
    (revocation propagation).

    The refresh token itself is NOT stored — only its ``jti`` claim,
    which lets refresh lookups find the session without exposing the
    bearer secret to anyone with DB read access.
    """

    __tablename__ = "oidc_refresh_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    jti: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    oidc_sub: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_validated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<OidcRefreshSession user_id={self.user_id!r} "
            f"jti={self.jti[:8]!r}... sub={self.oidc_sub!r}>"
        )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class Project(Base):
    """A project managed within an organization."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="projects"
    )
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by])
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="project")

    def __repr__(self) -> str:
        return f"<Project id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


class Task(Base):
    """A task (spec) belonging to a project."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="backlog"
    )
    spec_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    assigned_to: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="tasks")
    creator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[created_by]
    )
    assignee: Mapped["User | None"] = relationship(
        "User", foreign_keys=[assigned_to]
    )

    def __repr__(self) -> str:
        return f"<Task id={self.id!r} title={self.title!r} status={self.status!r}>"


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------


class ApiKey(Base):
    """API key for programmatic access, scoped to a user and organization."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="api_keys")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="api_keys"
    )

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Git Credentials (encrypted PATs for cloning private repos — epic #82 PR-C)
# ---------------------------------------------------------------------------


class GitCredential(Base):
    """Stored Git credential for the portal-managed clone flow (#82 PR-C).

    V1 supports HTTPS personal-access-token (PAT) credentials only. Deploy
    Keys (SSH) and GitHub App install IDs (short-lived tokens) are out of
    scope for V1 — both are tracked as follow-ups on epic #82.

    The token is encrypted at rest via ``EncryptedString`` (Epic #26 P2).
    Scope is **per-org** rather than per-user: anyone with rights on the
    org can use the credential to clone — matches how teams typically
    share Deploy Keys today.

    Per-project binding happens via ``ProjectCreate.gitCredentialId``
    (already accepted by the API since PR-A — wired in this PR-C). The
    credential's ``host`` field is informational only (e.g. ``github.com``,
    ``gitlab.example.internal``); URL matching is the caller's job.
    """

    __tablename__ = "git_credentials"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    # Human-readable label, e.g. "github-deploy-bot" or "gitlab-readonly".
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Credential kind. V1: ``pat`` only. ``deploy_key`` and ``github_app``
    # land in later follow-ups; the enum-by-convention keeps the column
    # forward-compatible without a migration.
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="pat")
    # Informational host (no enforcement) — surfaces in the UI so users
    # can tell which credential applies to which project.
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # GitHub PATs prefer the ``oauth2`` username; GitLab PATs use ``oauth2``
    # too. Empty/None means "username portion not needed" (rare).
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The actual token — never logged, never returned via API after creation.
    token: Mapped[str] = mapped_column(_EncryptedString(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped["Organization"] = relationship("Organization")

    def __repr__(self) -> str:
        return f"<GitCredential id={self.id!r} name={self.name!r}>"


class TestTargetCredential(Base):
    """An encrypted credential used to authenticate to a system-under-test (#107).

    Mirrors :class:`GitCredential`: org-scoped, secret columns encrypted at
    rest via ``EncryptedString`` (Epic #26 P2 — KMS/Vault/Azure/GCP backends),
    and the secret is never returned via the API after creation (only
    metadata). Generated tests reference these by ``name`` from
    ``.pfactory.yml`` ``test_credentials``; the broker resolves them and the
    executor injects them as ephemeral env into egress-enabled lanes only.

    ``username`` is plaintext (not sensitive on its own). ``secret`` holds the
    password / API token / TOTP seed; ``extra`` is an optional encrypted JSON
    blob for kind-specific fields (e.g. ``{"otp_period": 30}``). ``kind`` is an
    enum-by-convention (``form`` | ``api_token`` | ``basic_auth`` | ``totp``)
    validated at the API layer, keeping the column forward-compatible.
    """

    __tablename__ = "test_target_credentials"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default="form")
    # Plaintext username/identifier (not a secret on its own).
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The secret material — password / API token / TOTP seed. Encrypted at rest.
    secret: Mapped[str] = mapped_column(_EncryptedString(), nullable=False)
    # Optional kind-specific JSON (encrypted), e.g. {"otp_period": 30}.
    extra: Mapped[str | None] = mapped_column(_EncryptedString(), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped["Organization"] = relationship("Organization")

    # A credential name is unique per org so .pfactory.yml refs are unambiguous.
    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_test_cred_org_name"),
    )

    def __repr__(self) -> str:
        return f"<TestTargetCredential id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# Email Accounts (OAuth-connected email for notifications)
# ---------------------------------------------------------------------------


class EmailAccount(Base):
    """OAuth-connected email account for sending notifications."""

    __tablename__ = "email_accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", name="uq_email_accounts_user_provider"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "outlook" | "gmail"
    email_address: Mapped[str] = mapped_column(String(255), nullable=False)
    # P2.3: OAuth credentials encrypted at rest via EncryptedString.
    # See apps/web-server/server/crypto/ for the at-rest encryption layer.
    access_token: Mapped[str] = mapped_column(
        _EncryptedString(), nullable=False
    )
    refresh_token: Mapped[str | None] = mapped_column(
        _EncryptedString(), nullable=True
    )
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            f"<EmailAccount id={self.id!r} provider={self.provider!r} "
            f"email={self.email_address!r}>"
        )


# ---------------------------------------------------------------------------
# LLM Endpoints (OpenAI-compatible user-defined endpoints)
# ---------------------------------------------------------------------------


class LLMEndpoint(Base):
    """User-defined OpenAI-compatible LLM endpoint (LM Studio, vLLM, OpenRouter, etc.)."""

    __tablename__ = "llm_endpoints"
    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_llm_endpoints_user_label"),
        Index("ix_llm_endpoints_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # P2.3: provider API key encrypted at rest via EncryptedString.
    api_key: Mapped[str | None] = mapped_column(_EncryptedString(), nullable=True)
    default_model: Mapped[str] = mapped_column(String(255), nullable=False)
    headers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            f"<LLMEndpoint id={self.id!r} label={self.label!r} "
            f"base_url={self.base_url!r}>"
        )


class DocsTargetConnection(Base):
    """User's Backstage / Confluence connection for the plan→docs emit (P4b).

    Mirrors :class:`LLMEndpoint`: user-scoped, the API token encrypted at rest,
    unique ``(user_id, label)``. ``enabled_by_default`` makes this target part of
    the default emit set; a per-plan selection can still override it.
    """

    __tablename__ = "docs_target_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_docs_target_user_label"),
        Index("ix_docs_target_connections_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)  # backstage | confluence
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_token: Mapped[str | None] = mapped_column(_EncryptedString(), nullable=True)
    space: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Confluence
    enabled_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            f"<DocsTargetConnection id={self.id!r} kind={self.kind!r} "
            f"label={self.label!r}>"
        )


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Immutable audit trail for security-relevant actions."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_id", "org_id"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    # Epic #26 P5.1 — daily retention job deletes rows where
    # retention_until <= now(). Default policy: 13 months (SOC2 12mo +
    # buffer); set per-row at write time so the policy can vary by
    # action class (login events: short, security events: long).
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    # Epic #26 P5.2 — Per-row hash chain. SHA-256 of the previous
    # row's content (or the genesis sentinel for the first row).
    # Threat model: tamper-detection within the audit log only.
    # Signed external anchor = v1.1.
    prev_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Relationships (read-only lookups, no back_populates needed)
    organization: Mapped["Organization | None"] = relationship(
        "Organization", foreign_keys=[org_id]
    )
    user: Mapped["User | None"] = relationship(
        "User", foreign_keys=[user_id]
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id!r} action={self.action!r} "
            f"resource_type={self.resource_type!r}>"
        )


# ---------------------------------------------------------------------------
# P2.2 — KMS data keys (per-organization, wrapped by KMS root key)
# ---------------------------------------------------------------------------


class KmsDataKey(Base):
    """Per-organization data key, wrapped by the active KMS root.

    Each organization gets one (and only one) active row. Workflow:
      1. App generates a random 32-byte data key.
      2. The active KMS backend (`crypto.kms.get_backend()`) encrypts the
         data key under the root key, producing `wrapped_key`.
      3. EncryptedString columns scoped to that org are encrypted under
         the data key (P2.3 wires the binding).
      4. KMS root rotation re-wraps `wrapped_key` and bumps `rotated_at`
         so the in-process LRU cache (DataKeyManager) re-fetches (P2.5).
    """

    __tablename__ = "kms_data_keys"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kms_key_id: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Identifier of the KMS root key that wrapped this data key. "
                "For fernet backend: literal `fernet:default`. For aws_kms: "
                "the KMS ARN. Lets rotation runbooks know which backend "
                "wrapped each row.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
        comment="Updated on every re-wrap (root key rotation). The "
                "DataKeyManager polls this column to invalidate its "
                "in-process LRU cache.",
    )

    def __repr__(self) -> str:
        return (
            f"<KmsDataKey id={self.id!r} org_id={self.org_id!r} "
            f"kms_key_id={self.kms_key_id!r}>"
        )
