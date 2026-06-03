#!/usr/bin/env bash
# Ship-readiness drill: image-mirror via cosign copy (Epic #26 P7.6).
#
# Mirrors an PFactory image to a private registry preserving the
# cosign signature, then verifies the mirrored image still verifies
# against the upstream OIDC identity.
#
# Modes:
#   --dry-run  : Print the cosign commands; don't execute.
#   (no flag)  : Live mirror. Requires:
#                  SOURCE_IMAGE          — e.g. ghcr.io/olafkfreund/pfactory:1.0.0
#                  TARGET_REGISTRY       — e.g. registry.internal.bank.com/pfactory
#                  Push creds for TARGET_REGISTRY
#                  cosign 2.2+ on PATH
#
# Usage:
#   scripts/drills/image-mirroring.sh --help
#   scripts/drills/image-mirroring.sh --dry-run

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
image-mirroring.sh — PFactory v1.0 P7.6 image-mirroring drill

USAGE
  image-mirroring.sh [--dry-run] [--help]

ENVIRONMENT
  SOURCE_IMAGE         e.g. ghcr.io/olafkfreund/pfactory:1.0.0
  TARGET_REGISTRY      e.g. registry.internal.bank.com/pfactory:1.0.0
  COSIGN_VERIFY_OIDC   Expected OIDC issuer for upstream signature
                       (defaults to https://token.actions.githubusercontent.com)
  COSIGN_VERIFY_IDENTITY  Expected identity (defaults to the GitHub Actions
                          workflow ref pattern)

EXIT
  0  mirror + verify succeeded
  1  verification failed at mirrored URL
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
    echo "=== PFactory image-mirroring drill ==="
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "Mode: DRY-RUN"
    else
        echo "Mode: LIVE"
        : "${SOURCE_IMAGE:?SOURCE_IMAGE is required for live mode}"
        : "${TARGET_REGISTRY:?TARGET_REGISTRY is required for live mode}"
    fi

    step "1. Pre-flight: verify upstream signature on SOURCE_IMAGE"
    if [[ "$DRY_RUN" == "0" ]]; then
        cosign verify \
            --certificate-oidc-issuer "${COSIGN_VERIFY_OIDC:-https://token.actions.githubusercontent.com}" \
            --certificate-identity-regexp "${COSIGN_VERIFY_IDENTITY:-https://github.com/olafkfreund/PFactory/.+}" \
            "$SOURCE_IMAGE"
    fi

    step "2. Copy image + signature + SBOM via cosign copy"
    if [[ "$DRY_RUN" == "0" ]]; then
        cosign copy "$SOURCE_IMAGE" "$TARGET_REGISTRY"
    fi

    step "3. Verify the mirrored image's signature still validates"
    if [[ "$DRY_RUN" == "0" ]]; then
        cosign verify \
            --certificate-oidc-issuer "${COSIGN_VERIFY_OIDC:-https://token.actions.githubusercontent.com}" \
            --certificate-identity-regexp "${COSIGN_VERIFY_IDENTITY:-https://github.com/olafkfreund/PFactory/.+}" \
            "$TARGET_REGISTRY"
    fi

    step "4. Report digests match"
    if [[ "$DRY_RUN" == "0" ]]; then
        SRC_DIGEST=$(cosign triangulate "$SOURCE_IMAGE" 2>/dev/null | \
            grep -oP 'sha256:[a-f0-9]+' | head -1)
        TGT_DIGEST=$(cosign triangulate "$TARGET_REGISTRY" 2>/dev/null | \
            grep -oP 'sha256:[a-f0-9]+' | head -1)
        if [[ "$SRC_DIGEST" != "$TGT_DIGEST" ]]; then
            echo "FAIL: digest mismatch ($SRC_DIGEST vs $TGT_DIGEST)"
            exit 1
        fi
        echo "OK: digests match ($SRC_DIGEST)"
    fi

    echo "=== drill complete ==="
}

main "$@"
