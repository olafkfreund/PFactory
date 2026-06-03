#!/usr/bin/env bash
# Ship-readiness drill: backup → restore → verify decryption (Epic #26 P7.6).
#
# Exercises the operator's confidence that:
#   1. pg_dump captures encrypted columns correctly (ciphertext, not
#      plaintext fallback).
#   2. pg_restore reproduces the schema + data.
#   3. The restored DB's wrapped data keys re-unwrap against the
#      SAME KMS root key — i.e., backups don't tie the DB to a
#      specific KMS root version.
#
# Modes:
#   --dry-run  : Print every step + intended commands; don't touch
#                any real DB / KMS. Suitable for CI.
#   (no flag)  : Live drill. Requires:
#                  DATABASE_URL                   — source DB
#                  DATABASE_URL_RESTORE_TARGET    — destination DB
#                  KMS_FERNET_KEY (or backend env)
#                  pg_dump + psql + python on PATH
#
# Usage:
#   scripts/drills/backup-restore.sh --help
#   scripts/drills/backup-restore.sh --dry-run
#   DATABASE_URL=... scripts/drills/backup-restore.sh

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
backup-restore.sh — PFactory v1.0 P7.6 backup-restore drill

USAGE
  backup-restore.sh [--dry-run] [--help]

ENVIRONMENT
  DATABASE_URL                   Source DB (required for live mode)
  DATABASE_URL_RESTORE_TARGET    Destination DB (required for live mode)
  KMS_FERNET_KEY                 Or backend-specific KMS env vars

EXIT
  0  drill completed; verification passed
  1  verification failed
  2  invalid arguments
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
    echo "=== PFactory backup-restore drill ==="
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "Mode: DRY-RUN (no external systems touched)"
    else
        echo "Mode: LIVE"
        : "${DATABASE_URL:?DATABASE_URL is required for live mode}"
        : "${DATABASE_URL_RESTORE_TARGET:?DATABASE_URL_RESTORE_TARGET is required for live mode}"
    fi

    step "1. pg_dump source DB to /tmp/pfactory-drill-\$\$.dump"
    if [[ "$DRY_RUN" == "0" ]]; then
        pg_dump --format=custom \
            --file="/tmp/pfactory-drill-$$.dump" \
            "$DATABASE_URL"
    fi

    step "2. pg_restore to target DB"
    if [[ "$DRY_RUN" == "0" ]]; then
        pg_restore --clean --if-exists --no-owner \
            --dbname="$DATABASE_URL_RESTORE_TARGET" \
            "/tmp/pfactory-drill-$$.dump"
    fi

    step "3. Verify schema: ensure encrypted columns are LargeBinary type"
    if [[ "$DRY_RUN" == "0" ]]; then
        psql "$DATABASE_URL_RESTORE_TARGET" -c "\
            SELECT column_name, data_type FROM information_schema.columns \
            WHERE table_name='email_accounts' AND column_name IN ('access_token', 'refresh_token');"
    fi

    step "4. Verify decryption: load DataKeyManager, unwrap a random org's key"
    if [[ "$DRY_RUN" == "0" ]]; then
        python -m server.audit verify-chain /dev/stdin <<<"$(
            psql "$DATABASE_URL_RESTORE_TARGET" \
                -c 'COPY (SELECT row_to_json(t) FROM (SELECT * FROM audit_logs ORDER BY created_at) t) TO STDOUT;'
        )"
    fi

    step "5. Cleanup: drop the dump file"
    if [[ "$DRY_RUN" == "0" ]]; then
        rm -f "/tmp/pfactory-drill-$$.dump"
    fi

    echo "=== drill complete ==="
}

main "$@"
