# Database Schema

> Spec: Test-Target Authentication + Credential Vault
> Created: 2026-06-02

## New table: `test_target_credentials`

Mirrors `GitCredential` (`apps/web-server/server/database/models.py:383`) — same encryption, org-scoping, and "never returned after create" contract.

```python
class TestTargetCredential(Base):
    """An encrypted credential used to authenticate to a system-under-test.

    Org-scoped (anyone with rights on the org may reference it from
    .pfactory.yml). The secret columns use _EncryptedString (Epic #26 P2),
    so they are KMS/Vault/Azure/GCP-encrypted at rest. The secret is never
    returned via the API after creation — only metadata.
    """
    __tablename__ = "test_target_credentials"

    id: Mapped[str] = mapped_column(primary_key=True, default=_uuid)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)

    name: Mapped[str] = mapped_column(nullable=False)              # referenced from .pfactory.yml
    kind: Mapped[str] = mapped_column(nullable=False)              # form | api_token | basic_auth | totp

    username: Mapped[str | None] = mapped_column(nullable=True)    # plaintext OK (not a secret)
    secret: Mapped[str] = mapped_column(_EncryptedString(), nullable=False)        # password / token / totp-seed
    extra: Mapped[str | None] = mapped_column(_EncryptedString(), nullable=True)   # encrypted JSON (e.g. {"otp_period": 30})

    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_test_cred_org_name"),)
```

## Constraints & indexes

- `UNIQUE(org_id, name)` — a credential `name` is unique per org so `.pfactory.yml` refs are unambiguous.
- `INDEX(org_id)` — list-by-org is the hot path.
- `kind` validated at the API layer against `{form, api_token, basic_auth, totp}` (not a DB enum, for forward-compat — same approach the codebase uses elsewhere).

## Migration

- Alembic revision adding `test_target_credentials` (the repo's P2 migrations auto-apply via `MIGRATIONS_AUTO_APPLY`, see web-server `config.py`).
- Down-migration drops the table. No backfill (new feature).
- The `postgres (P1 acceptance)` CI job exercises migrations against PG 15 + 16 — the new revision must pass there.

## What is NOT stored

- No per-test session cookies / `storageState` blobs in the DB — those are ephemeral, produced per-run in `/scratch/.auth/` and wiped. Only the *input* credential is persisted.
- No plaintext secret anywhere; `username` is the only plaintext column (it is not sensitive on its own).
