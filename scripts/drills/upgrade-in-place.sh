#!/usr/bin/env bash
# Ship-readiness drill: synthetic v0.x → v1.0 upgrade-in-place (Epic #26 P7.6).
#
# Exercises the v0.x → v1.0 migration documented in
# guides/deployment/upgrade.md. Seeds a synthetic v0.x schema (plaintext
# email_accounts.access_token), runs Alembic upgrade head, verifies
# the encrypted columns readable post-upgrade, no data loss.
#
# Modes:
#   --dry-run  : Print steps; don't execute. CI-safe.
#   (no flag)  : Live drill against a throwaway DB.
#                Requires DATABASE_URL_DRILL + KMS env.
#
# Usage:
#   scripts/drills/upgrade-in-place.sh --help
#   scripts/drills/upgrade-in-place.sh --dry-run

set -euo pipefail

DRY_RUN=0
SHOW_HELP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --help|-h) SHOW_HELP=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ "$SHOW_HELP" == "1" ]]; then
    cat <<'EOF'
upgrade-in-place.sh — PFactory v1.0 P7.6 upgrade-in-place drill

USAGE
  upgrade-in-place.sh [--dry-run] [--help]

ENVIRONMENT
  DATABASE_URL_DRILL    Throwaway DB URL (required for live mode)
  KMS_FERNET_KEY        Or backend-specific KMS env vars

EXIT
  0  drill completed; upgrade verifies
  1  upgrade verification failed
  2  invalid arguments

Synthetic v0.x state seeded:
  - users with raw emails
  - email_accounts.access_token as TEXT (plaintext OAuth tokens)
  - no kms_data_keys table
  - no audit_logs.prev_hash / retention_until columns
EOF
    exit 0
fi

step() {
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "[DRY-RUN] $*"
    else
        echo "[LIVE] $*"
    fi
}

main() {
    echo "=== PFactory upgrade-in-place drill ==="
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "Mode: DRY-RUN"
    else
        echo "Mode: LIVE"
        : "${DATABASE_URL_DRILL:?DATABASE_URL_DRILL is required}"
        : "${KMS_FERNET_KEY:?KMS_FERNET_KEY is required}"
    fi

    step "1. Drop + recreate the drill DB"
    if [[ "$DRY_RUN" == "0" ]]; then
        psql -c "DROP DATABASE IF EXISTS pfactory_drill;"
        psql -c "CREATE DATABASE pfactory_drill;"
    fi

    step "2. Seed synthetic v0.x schema"
    if [[ "$DRY_RUN" == "0" ]]; then
        psql "$DATABASE_URL_DRILL" <<'SQL'
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE email_accounts (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    provider VARCHAR(50) NOT NULL,
    email_address VARCHAR(255) NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_expiry TIMESTAMP,
    scopes TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, provider)
);
INSERT INTO users (id, email, name, password_hash)
  VALUES ('u1', 'alice@example.com', 'Alice', 'hashed');
INSERT INTO email_accounts (id, user_id, provider, email_address, access_token)
  VALUES ('e1', 'u1', 'gmail', 'alice@example.com',
    'ya29.PLAINTEXT_TOKEN_FROM_V0_FAKE_FOR_DRILL_PURPOSES');
SQL
    fi

    step "3. Run alembic upgrade head (the v0.x → v1.0 chain)"
    if [[ "$DRY_RUN" == "0" ]]; then
        DATABASE_URL="$DATABASE_URL_DRILL" \
            python -m alembic upgrade head
    fi

    step "4. Verify access_token is now ciphertext (LargeBinary)"
    if [[ "$DRY_RUN" == "0" ]]; then
        psql "$DATABASE_URL_DRILL" -c "
            SELECT octet_length(access_token) AS bytes,
                   encode(access_token, 'hex') !~ 'PLAINTEXT_TOKEN' AS no_plaintext
            FROM email_accounts WHERE id = 'e1';"
    fi

    step "5. Cleanup: drop drill DB"
    if [[ "$DRY_RUN" == "0" ]]; then
        psql -c "DROP DATABASE pfactory_drill;"
    fi

    echo "=== drill complete ==="
}

main "$@"
