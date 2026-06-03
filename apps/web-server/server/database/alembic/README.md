# AIFactory Alembic migrations

This directory holds the schema migrations for AIFactory's web-server
Postgres database (`apps/web-server/server/database/`).

## Running migrations

```bash
# From apps/web-server/, with DATABASE_URL set:
python -m alembic upgrade head     # apply pending migrations
python -m alembic current          # show the current revision
python -m alembic history          # show the migration graph
```

Production deployments **must** apply migrations via the Helm
``alembic-upgrade`` Job, not at application boot. See
``APP_MIGRATIONS_AUTO_APPLY=false`` in the chart values.

## Migration list

| Revision | Title | Notes |
| --- | --- | --- |
| `1b386c99e615` | baseline initial schema | Portable defaults (`CURRENT_TIMESTAMP`). |
| `a4c2e9f8b1d3` | kms_data_keys | Per-org wrap-key storage (Epic #26 P2.2). |
| `c6e3b2d4a8f0` | encrypt credential columns | **⚠ FORWARD-ONLY ⚠** see below. |

## ⚠ FORWARD-ONLY MIGRATION: `c6e3b2d4a8f0_encrypt_credentials`

This migration converts plaintext credential columns to ciphertext via
the `EncryptedString` TypeDecorator (Epic #26 P2.3). The columns
affected:

- `email_accounts.access_token`
- `email_accounts.refresh_token`
- `llm_endpoints.api_key`

**Once this migration runs, the plaintext credentials are gone.** The
`downgrade()` path raises `NotImplementedError` because reversing
requires the data to still be decryptable — which means having the
KMS root key plus the same per-org data keys, both of which we
deliberately don't preserve in a downgrade path.

### To downgrade past this revision

1. **Stop the application** (no writes to credential columns during DR).
2. **Restore the database from a `pg_dump` backup taken IMMEDIATELY
   BEFORE this migration ran.** This is the only supported recovery
   path.
3. Re-run `alembic upgrade` to apply only the migrations you want,
   stopping before `c6e3b2d4a8f0`.

### Operator pre-flight before applying

- [ ] `pg_dump` of the entire database taken and verified restorable.
- [ ] `KMS_FERNET_KEY` (or the equivalent for your `APP_KMS_BACKEND`)
      exported in the migration runner's env. The migration uses the
      active KMS backend to encrypt rows in place — running with an
      unset/wrong key fails fast with `KMS_FERNET_KEY env var is not
      set` rather than silently producing garbage.
- [ ] The running identity has `Encrypt` permission on the configured
      KMS root (cloud backends).
- [ ] Read the [kms-rotation-runbook.md](../../../../../guides/operations/kms-rotation-runbook.md)
      and [encrypted-secrets-dr.md](../../../../../guides/operations/encrypted-secrets-dr.md)
      so you know what to do if the migration fails partway through.

## Why forward-only?

A reversible downgrade would have to write plaintext credentials back
to disk. That defeats the entire point of the encryption-at-rest
layer: an attacker with read access to the alembic CLI could
trivially decrypt the database. Forward-only is the correct security
posture; restore-from-backup is the correct recovery path.

This pattern matches how AWS RDS, GCP Cloud SQL, and Azure SQL handle
their own at-rest encryption migrations — once the data is encrypted,
the only "downgrade" is a point-in-time restore.
